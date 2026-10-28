"""Local business tools. All write constraints are enforced here, not by prompts."""
import copy
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .domain import ValidationError, instant
from .constraints import violations


PARAMS = {
    'read_material': {'material_id': str},
    'check_rules': {'material_id': str},
    'query_slots': {'material_id': str, 'start_time': str, 'count': int},
    'save_draft': {'material_id': str, 'suggested_time': str},
}


def load_world(directory):
    directory = Path(directory)
    world = {n: json.loads((directory / f'{n}.json').read_text(encoding='utf-8'))
             for n in ('materials', 'rules', 'schedule')}
    if not isinstance(world['materials'], dict) or not isinstance(world['rules'], dict) or not isinstance(world['schedule'], list):
        raise ValidationError('invalid world structure')
    rules = world['rules']
    for key in ('required_fields', 'blocked_claims', 'manual_review_tags', 'channels'):
        if not isinstance(rules.get(key), list) or not all(isinstance(x, str) for x in rules[key]):
            raise ValidationError('invalid rules: ' + key)
    for m in world['materials'].values():
        if not isinstance(m, dict):
            raise ValidationError('material must be an object')
    for entry in world['schedule']:
        if not isinstance(entry, dict) or not isinstance(entry.get('channel'), str):
            raise ValidationError('invalid schedule entry')
        instant(entry.get('time'))
    return world


class Tools:
    def __init__(self, request, world, store, failures=None):
        self.request, self.world, self.store = request, world, store
        self.failures = dict(failures or {})
        self.store.claim(request)

    def call(self, name, args):
        if not isinstance(name, str) or name not in PARAMS:
            return {'ok': False, 'error': 'unknown_tool'}
        spec = PARAMS[name]
        if not isinstance(args, dict) or set(args) != set(spec) or any(type(args[k]) is not typ for k, typ in spec.items()):
            return {'ok': False, 'error': 'invalid_arguments'}
        try:
            self.request.item(args['material_id'])
            if name == 'query_slots' and not 1 <= args['count'] <= 6:
                raise ValidationError('count must be 1..6')
            remaining = self.failures.get(name, 0)
            if remaining:
                if remaining > 0:
                    self.failures[name] -= 1
                return {'ok': False, 'error': 'tool_unavailable', 'tool': name,
                        'message': 'Injected synthetic failure; no side effect.'}
            return getattr(self, name)(**args)
        except (ValueError, TypeError, OverflowError) as exc:
            return {'ok': False, 'error': 'invalid_arguments', 'message': str(exc)}
        except sqlite3.Error:
            return {'ok': False, 'error': 'storage_error', 'message': 'Database operation failed; success not confirmed.'}

    def read_material(self, material_id):
        material = self.world['materials'].get(material_id)
        if material is None:
            return {'ok': False, 'error': 'material_not_found', 'material_id': material_id}
        return {'ok': True, 'material_id': material_id, 'material': copy.deepcopy(material)}

    def check_rules(self, material_id):
        result = self.read_material(material_id)
        if not result['ok']:
            return result
        m, rules = result['material'], self.world['rules']
        missing = [f for f in rules['required_fields'] if f not in m or m[f] is None or m[f] == '']
        risks = []
        if not missing:
            if not isinstance(m.get('title'), str) or not isinstance(m.get('channel'), str) or type(m.get('rights_confirmed')) is not bool:
                missing.append('valid_metadata_types')
        claims, tags = m.get('claims', []), m.get('tags', [])
        if not isinstance(claims, list) or not all(isinstance(x, str) for x in claims):
            missing.append('valid_claims')
            claims = []
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
            missing.append('valid_tags')
            tags = []
        if m.get('rights_confirmed') is False:
            risks.append({'code': 'rights_unconfirmed', 'severity': 'block', 'reason': 'Copyright permission is explicitly absent.'})
        if isinstance(m.get('channel'), str) and m['channel'] not in rules['channels']:
            risks.append({'code': 'unsupported_channel', 'severity': 'block', 'reason': 'Channel is outside synthetic policy.'})
        for claim in claims:
            if any(blocked.casefold() in claim.casefold() for blocked in rules['blocked_claims']):
                risks.append({'code': 'unsupported_claim', 'severity': 'block', 'reason': claim})
        if set(tags) & set(rules['manual_review_tags']):
            risks.append({'code': 'manual_review', 'severity': 'review', 'reason': 'Human review required for tagged material.'})
        status = ('blocked' if any(r['severity'] == 'block' for r in risks) else
                  'needs_input' if missing else 'needs_review' if risks else 'approved')
        return {'ok': True, 'material_id': material_id, 'review_status': status,
                'missing_fields': missing, 'risks': risks, 'rule_version': rules.get('version', 'unknown')}

    def query_slots(self, material_id, start_time, count):
        m = self.read_material(material_id)
        if not m['ok']:
            return m
        channel = m['material'].get('channel')
        if not isinstance(channel, str) or not channel:
            return {'ok': False, 'error': 'missing_channel'}
        start = datetime.fromisoformat(instant(start_time))
        slots = []
        for i in range(count):
            candidate = (start + timedelta(hours=i)).isoformat()
            occupied = not self.store.available(channel, candidate, self.world['schedule'],
                                                 (self.request.request_id, material_id))
            reasons = self.time_violations(material_id, candidate)
            slots.append({'time': candidate, 'occupied': occupied, 'constraint_violations': reasons,
                          'available': not occupied and not reasons})
        return {'ok': True, 'material_id': material_id, 'channel': channel, 'slots': slots}

    def time_violations(self, material_id, candidate):
        item = self.request.item(material_id)
        candidate = instant(candidate)
        reasons = violations(item, candidate)
        # A flexible item must not consume a viable exact slot needed by another batch item.
        own_target = self.request.confirmations.get(material_id, item['requested_time'])
        if item['hard_time'] and candidate == own_target:
            return reasons
        channel = self.world['materials'].get(material_id, {}).get('channel')
        saved = {d['material_id'] for d in self.store.drafts(self.request.request_id)}
        for other in self.request.items:
            mid = other['material_id']
            target = self.request.confirmations.get(mid, other['requested_time'])
            if mid == material_id or mid in saved or not other['hard_time'] or target != candidate:
                continue
            if self.world['materials'].get(mid, {}).get('channel') != channel or violations(other, target):
                continue
            review = self.check_rules(mid)
            if review.get('ok') and review['review_status'] in ('approved', 'needs_review'):
                reasons.append('reserved_for_fixed_item:' + mid)
        return reasons

    def save_draft(self, material_id, suggested_time):
        time = instant(suggested_time)
        review = self.check_rules(material_id)
        if not review['ok']:
            return review
        if review['review_status'] in ('needs_input', 'blocked'):
            return {'ok': False, 'error': 'review_not_passed', 'review': review}
        item = self.request.item(material_id)
        reasons = self.time_violations(material_id, time)
        if reasons:
            return {'ok': False, 'error': 'time_constraint_violation', 'violations': reasons, 'review': review}
        pending = []
        if item['hard_time'] and time != item['requested_time'] and self.request.confirmations.get(material_id) != time:
            pending.append('time_change')
        if review['review_status'] == 'needs_review':
            pending.append('manual_review')
        result = self.store.save(self.request, item, self.world['materials'][material_id], time,
                                 pending, self.world['schedule'])
        result['review'] = review
        return result

    def validate_saved(self, draft):
        """Final safety audit; never changes the historical draft or executes a model action."""
        mid = draft['material_id']
        review = self.check_rules(mid)
        problems = []
        problems.extend(self.time_violations(mid, draft['suggested_time']))
        if not review.get('ok') or review.get('review_status') in ('blocked', 'needs_input'):
            problems.append('saved_material_no_longer_approved')
        elif review.get('review_status') == 'needs_review' and 'manual_review' not in draft['pending_items']:
            problems.append('saved_material_needs_manual_review')
        material = self.world['materials'].get(mid, {})
        if material.get('channel') != draft['channel']:
            problems.append('saved_channel_changed')
        if not self.store.available(draft['channel'], draft['suggested_time'], self.world['schedule'],
                                    (self.request.request_id, mid)):
            problems.append('saved_slot_now_conflicts')
        return {'source': 'final_safety_recheck', 'valid': not problems, 'problems': problems, 'review': review}
