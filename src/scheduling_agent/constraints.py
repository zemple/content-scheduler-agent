"""Deterministic time guards shared by query, save and final validation."""
from .domain import instant


def violations(item, candidate):
    time = instant(candidate)
    reasons = []
    if item.get('not_before') and time < item['not_before']:
        reasons.append('before_window')
    if item.get('not_after') and time > item['not_after']:
        reasons.append('after_window')
    if item.get('deadline') and time >= item['deadline']:
        reasons.append('at_or_after_deadline')
    if item.get('adjustment') == 'earlier_only' and time > item['requested_time']:
        reasons.append('later_than_preferred')
    if item.get('adjustment') == 'later_only' and time < item['requested_time']:
        reasons.append('earlier_than_preferred')
    return reasons
