#!/usr/bin/env python3
"""Check committed evidence against artifacts, not just summary pass flags."""
import hashlib
import json
from pathlib import Path

from reproduce import CASES

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit():
    checked = 0
    for mode in ('mock', 'live'):
        directory = ROOT / 'examples' / mode
        summary = read(directory / 'summary.json')
        assert summary['mode'] == mode and summary['all_passed']
        assert {row['case'] for row in summary['cases']} == set(CASES)
        for name, spec in CASES.items():
            base = directory / name
            folders = [base / 'first', base / 'second'] if spec.get('repeat') else [base]
            ids = []
            for index, folder in enumerate(folders):
                result = read(folder / 'output.json')
                execution = read(folder / 'execution.json')
                drafts = read(folder / 'drafts.json')
                req = read(folder / 'input.json')
                events = [json.loads(s) for s in (folder / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
                world = {n: read(folder / 'data' / (n + '.json')) for n in ('materials', 'rules', 'schedule')}
                digest = hashlib.sha256(json.dumps(world, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                expected = spec.get('second_status', spec['status']) if index else spec['status']
                assert result['task_status'] == expected, (mode, name, 'status')
                assert result['mode'] == mode and result['data_sha256'] == digest, (mode, name, 'data hash')
                assert result['request_id'] == req['request_id']
                assert result['steps'] == len(events) <= result['max_steps']
                assert len(drafts) == spec['saved'] == execution['database_rows']
                assert len({d['draft_id'] for d in drafts}) == len(drafts)
                assert all(i['publication_status'] == 'not_published' for i in result['items'])
                for i, event in enumerate(events):
                    assert event['step'] == i + 1
                    assert event['reason'] and event['result_summary']
                    assert event['next_action'] == (events[i + 1]['tool'] if i + 1 < len(events) else 'stop:' + result['termination'])
                    assert event['tool'] != 'publish'
                    if event['tool'] == 'save_draft' and event['observation'].get('ok'):
                        assert any(d['draft_id'] == event['observation']['draft']['draft_id'] for d in drafts)
                for item in result['items']:
                    assert (item['draft_status'] == 'saved') == any(d['draft_id'] == item['draft_id'] for d in drafts)
                    if item['confirmation_status'] == 'confirmed' and item['hard_time']:
                        assert item['suggested_time'] == item['requested_time'] or req['confirmations'].get(item['material_id']) == item['suggested_time']
                    if item['status'] == 'incomplete':
                        assert item['uncompleted_reasons']
                if mode == 'live':
                    assert any(e['model_metadata'].get('response_id') for e in events)
                if drafts:
                    ids.append(drafts[0]['draft_id'])
                checked += 1
            if spec.get('repeat'):
                assert len(set(ids)) == 1, (mode, name, 'idempotency')
            row = next(r for r in summary['cases'] if r['case'] == name)
            assert row['passed'] and row['actual_status'] == expected
    manifest = ROOT / 'examples' / 'source-manifest.json'
    if manifest.exists():
        for relative, digest in read(manifest)['source_sha256'].items():
            assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative
    print(f'PASS: {checked} actual execution records; data hashes, statuses, traces, drafts and idempotency checked.')


if __name__ == '__main__':
    audit()
