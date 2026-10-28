"""Input contracts and time normalization; no model-controlled authorization."""
import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone


class ValidationError(ValueError):
    pass


def instant(value):
    if not isinstance(value, str):
        raise ValidationError('time must be an ISO-8601 string')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValidationError('invalid ISO-8601 time') from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValidationError('timezone is required')
    try:
        utc = dt.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValidationError('time is outside the supported datetime range') from exc
    if utc.minute or utc.second or utc.microsecond:
        raise ValidationError('synthetic slots must start on a UTC hour')
    return utc.isoformat()


@dataclass(frozen=True)
class Request:
    request_id: str
    items: tuple
    confirmations: dict

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) - {'request_id', 'items', 'confirmations'}:
            raise ValidationError('request must contain request_id, items and optional confirmations')
        rid, items = value.get('request_id'), value.get('items')
        if not isinstance(rid, str) or not rid.strip() or len(rid) > 100:
            raise ValidationError('request_id must be 1..100 characters')
        if not isinstance(items, list) or not 1 <= len(items) <= 10:
            raise ValidationError('items must contain 1..10 materials')
        normalized, ids = [], set()
        for item in items:
            required = {'material_id', 'requested_time', 'hard_time'}
            optional = {'not_before', 'not_after', 'deadline', 'adjustment'}
            if not isinstance(item, dict) or not required <= set(item) or set(item) - required - optional:
                raise ValidationError('each item requires material_id, requested_time, hard_time')
            mid = item['material_id']
            if not isinstance(mid, str) or not mid.strip() or mid in ids:
                raise ValidationError('material IDs must be nonempty and unique')
            if type(item['hard_time']) is not bool:
                raise ValidationError('hard_time must be boolean')
            ids.add(mid)
            normalized_item = {**item, 'requested_time': instant(item['requested_time'])}
            if item.get('adjustment', 'either') not in ('either', 'earlier_only', 'later_only'):
                raise ValidationError('adjustment must be either, earlier_only or later_only')
            for name in ('not_before', 'not_after', 'deadline'):
                if name in item:
                    normalized_item[name] = instant(item[name])
            lower = normalized_item.get('not_before')
            upper = normalized_item.get('not_after')
            deadline = normalized_item.get('deadline')
            preferred = normalized_item['requested_time']
            if lower and upper and lower > upper:
                raise ValidationError('not_before must not be later than not_after')
            if lower and deadline and lower >= deadline:
                raise ValidationError('not_before must be strictly before deadline')
            if item.get('adjustment') == 'earlier_only' and lower and lower > preferred:
                raise ValidationError('earlier_only has no feasible slot in this window')
            if item.get('adjustment') == 'later_only' and ((upper and upper < preferred) or (deadline and deadline <= preferred)):
                raise ValidationError('later_only has no feasible slot in this window')
            normalized.append(normalized_item)
        confirmations = value.get('confirmations', {})
        if not isinstance(confirmations, dict) or set(confirmations) - ids:
            raise ValidationError('confirmations must map requested material IDs to explicit times')
        return cls(rid, tuple(copy.deepcopy(normalized)),
                   {mid: instant(t) for mid, t in confirmations.items()})

    def item(self, mid):
        for item in self.items:
            if item['material_id'] == mid:
                return item
        raise ValidationError('material is outside this request')

    def as_dict(self):
        return {'request_id': self.request_id, 'items': list(self.items),
                'confirmations': self.confirmations}

    def fingerprint(self):
        payload = self.as_dict()
        payload['items'] = sorted(payload['items'], key=lambda x: x['material_id'])
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
