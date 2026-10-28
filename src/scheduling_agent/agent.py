"""Bounded model -> tool -> observation loop; factual output is program-derived."""
import hashlib
import json
import time
from datetime import datetime, timezone

from .models import Action, ModelError


class Agent:
    def __init__(self, tools, model, max_steps=12):
        if type(max_steps) is not int or not 1 <= max_steps <= 100:
            raise ValueError('max_steps must be 1..100')
        self.tools, self.model, self.max_steps = tools, model, max_steps

    def run(self):
        events, termination = [], 'step_limit'
        started = time.monotonic()
        for step in range(1, self.max_steps + 1):
            try:
                action = self.model.decide(self.tools.request, events)
                if (not isinstance(action, Action) or not isinstance(action.tool, str) or not action.tool
                    or not isinstance(action.arguments, dict) or not isinstance(action.reason, str)):
                    raise ModelError('invalid_model_action')
            except ModelError as exc:
                events.append({'step': step, 'tool': 'model_error', 'arguments': {}, 'reason': '模型调用失败，保留已有结果。',
                               'observation': {'ok': False, 'error': str(exc)}, 'model_metadata': {}})
                termination = 'model_error'
                break
            if action.tool == 'finish':
                observation = {'ok': not action.arguments}
                if action.arguments:
                    observation['error'] = 'invalid_arguments'
                else:
                    unfinished = self.unfinished_reviewed_items(events)
                    if unfinished:
                        observation = {'ok': False, 'error': 'premature_finish',
                                       'unfinished_materials': unfinished,
                                       'message': 'These reviewed items still have unattempted feasible work. Choose next action; no tool was automatically executed.'}
            else:
                observation = self.tools.call(action.tool, action.arguments)
            events.append({'step': step, 'tool': action.tool, 'arguments': action.arguments,
                           'reason': action.reason, 'observation': observation,
                           'model_metadata': action.metadata})
            if action.tool == 'finish' and observation['ok']:
                termination = 'finished'
                break
        for i, event in enumerate(events):
            event['result_summary'] = self.summary(event['observation'])
            event['next_action'] = events[i + 1]['tool'] if i + 1 < len(events) else 'stop:' + termination
        return self.report(events, termination, started), events

    def unfinished_reviewed_items(self, events):
        saved = {d['material_id'] for d in self.tools.store.drafts(self.tools.request.request_id)}
        unfinished = []
        for item in self.tools.request.items:
            mid = item['material_id']
            own = [e for e in events if e['arguments'].get('material_id') == mid]
            reviews = [e['observation'] for e in own if e['tool']=='check_rules' and e['observation'].get('ok')]
            if mid in saved or not reviews or reviews[-1]['review_status'] not in ('approved','needs_review'):
                continue
            if any(not e['observation'].get('ok') for e in own):
                continue
            queries = [e['observation'] for e in own if e['tool']=='query_slots' and e['observation'].get('ok')]
            latest_slots = {s['time']:s['available'] for q in queries for s in q['slots']}
            if not queries or any(latest_slots.values()):
                unfinished.append(mid)
        return unfinished

    @staticmethod
    def summary(observation):
        if not observation.get('ok'):
            return observation.get('error', 'failed')
        if 'draft' in observation:
            return 'draft saved: ' + observation['draft']['confirmation_status']
        if 'review_status' in observation:
            return 'review: ' + observation['review_status']
        if 'slots' in observation:
            return f"available slots: {sum(s['available'] for s in observation['slots'])}/{len(observation['slots'])}"
        return 'ok'

    def report(self, events, termination, started):
        drafts = {d['material_id']: d for d in self.tools.store.drafts(self.tools.request.request_id)}
        items = []
        for item in self.tools.request.items:
            mid = item['material_id']
            own = [e for e in events if e['arguments'].get('material_id') == mid]
            reviews = [e['observation'] for e in own if e['tool'] == 'check_rules' and e['observation'].get('ok')]
            reviews += [e['observation']['review'] for e in own if 'review' in e['observation']]
            review = reviews[-1] if reviews else {}
            errors = [{'tool': e['tool'], 'error': e['observation'].get('error')} for e in own if not e['observation'].get('ok')]
            draft = drafts.get(mid)
            validation = self.tools.validate_saved(draft) if draft else None
            if validation:
                review = validation['review']
            latest_slots = {s['time']:s['available'] for e in own if e['tool'] == 'query_slots' and e['observation'].get('ok')
                            for s in e['observation']['slots']}
            suggestions = [time for time, available in latest_slots.items() if available]
            status = 'incomplete'
            if draft:
                status = 'awaiting_confirmation' if draft['confirmation_status'] == 'pending' else 'completed'
            elif review.get('review_status') in ('blocked', 'needs_input'):
                status = review['review_status']
            elif any(e['error'] == 'material_not_found' for e in errors):
                status = 'needs_input'
            save_attempted = any(e['tool'] == 'save_draft' for e in own)
            uncompleted = []
            if validation and not validation['valid']:
                uncompleted.extend(validation['problems'])
                status = review.get('review_status') if review.get('review_status') in ('blocked', 'needs_input') else 'incomplete'
            pending = list(draft['pending_items']) if draft else []
            suggestion = draft['suggested_time'] if draft else suggestions[0] if suggestions else None
            if suggestion and item['hard_time'] and suggestion != item['requested_time'] and self.tools.request.confirmations.get(mid) != suggestion:
                if 'time_change' not in pending:
                    pending.append('time_change')
            pending += ['provide:' + f for f in review.get('missing_fields', [])]
            if not draft and review.get('review_status') == 'needs_review':
                pending.append('manual_review')
            if any(e['error'] == 'material_not_found' for e in errors):
                pending.append('provide:material')
            if not draft:
                if save_attempted:
                    uncompleted.append('save_failed')
                if errors:
                    uncompleted.extend(sorted({e['error'] for e in errors if e['error']}))
                if review.get('review_status') in ('blocked', 'needs_input'):
                    uncompleted.append('review_' + review['review_status'])
                queries = [e for e in own if e['tool'] == 'query_slots' and e['observation'].get('ok')]
                if queries and not suggestions:
                    uncompleted.append('no_available_slot_in_checked_window')
                if status == 'incomplete' and not uncompleted:
                    uncompleted.append('model_stopped_without_save' if termination == 'finished' else termination)
            items.append({'material_id': mid, 'status': status,
                          'time_constraints': {k: item[k] for k in ('adjustment', 'not_before', 'not_after', 'deadline') if k in item},
                          'review_status': review.get('review_status', 'not_checked'),
                          'risk_reasons': review.get('risks', []), 'missing_fields': review.get('missing_fields', []),
                          'requested_time': item['requested_time'], 'hard_time': item['hard_time'],
                          'suggested_time': suggestion,
                          'pending_items': pending, 'draft_status': 'saved' if draft else 'failed' if save_attempted else 'not_saved',
                          'draft_id': draft['draft_id'] if draft else None,
                          'confirmation_status': ('needs_revalidation' if validation and not validation['valid'] else
                                                  draft['confirmation_status'] if draft else 'not_confirmed'),
                          'uncompleted_reasons': uncompleted, 'saved_draft_validation': validation,
                          'duplicate': any(e['observation'].get('duplicate', False) for e in own),
                          'publication_status': 'not_published', 'tool_errors': errors})
        statuses = {i['status'] for i in items}
        overall = next(iter(statuses)) if len(statuses) == 1 else 'partial'
        if termination != 'finished':
            overall = 'incomplete'
        return {'request_id': self.tools.request.request_id, 'mode': self.model.mode,
                'model': self.model.model_name, 'task_status': overall, 'termination': termination,
                'steps': len(events), 'max_steps': self.max_steps, 'items': items,
                'data_sha256': hashlib.sha256(json.dumps(self.tools.world, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                'run_at': datetime.now(timezone.utc).isoformat(),
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'limitation': 'Mock verifies workflow only, not real model decisions.' if self.model.mode == 'mock' else None}
