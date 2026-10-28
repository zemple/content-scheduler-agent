#!/usr/bin/env python3
"""Run richer scheduling cases through the real CLI; expectations never enter model input."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def source_hash():
    payload = b''.join(p.name.encode() + b'\0' + p.read_bytes() for p in sorted((ROOT/'src/scheduling_agent').glob('*.py')))
    return hashlib.sha256(payload).hexdigest()


def check_case(folder):
    """Independently inspect outputs, DB export, trace and all hard constraints."""
    expected, result = read(folder/'expectation.json'), read(folder/'output.json')
    req, drafts = read(folder/'input.json'), read(folder/'drafts.json')
    world = {name: read(folder/'data'/f'{name}.json') for name in ('materials','rules','schedule')}
    events = [json.loads(s) for s in (folder/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    checks = {'task_status': result['task_status'] == expected['status'],
              'draft_count': len(drafts) == expected['saved'],
              'steps_bounded': len(events) == result['steps'] <= result['max_steps'],
              'data_hash': result['data_sha256'] == hashlib.sha256(json.dumps(world,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
              'never_published': all(i['publication_status']=='not_published' for i in result['items']),
              'real_model_responses': result['mode'] != 'live' or all(e['model_metadata'].get('response_id') for e in events if e['tool']!='model_error'),
              'no_model_error': result['termination'] != 'model_error'}
    for mid, timestamp in expected.get('times',{}).items():
        selected = next((d for d in drafts if d['material_id']==mid),None)
        checks['time:'+mid] = bool(selected) and utc(selected['suggested_time']) == utc(timestamp)
    for i,event in enumerate(events):
        checks[f'trace:{i}'] = bool(event['reason']) and bool(event['result_summary']) and event['next_action'] == (events[i+1]['tool'] if i+1<len(events) else 'stop:'+result['termination'])
    occupied = {(s['channel'],utc(s['time'])) for s in world['schedule']}
    planned = set()
    for draft in drafts:
        mid = draft['material_id']
        item = next(x for x in req['items'] if x['material_id']==mid)
        t, preferred = utc(draft['suggested_time']), utc(item['requested_time'])
        valid = (draft['channel'],t) not in occupied and (draft['channel'],t) not in planned
        planned.add((draft['channel'],t))
        if item.get('not_before'): valid &= t >= utc(item['not_before'])
        if item.get('not_after'): valid &= t <= utc(item['not_after'])
        if item.get('deadline'): valid &= t < utc(item['deadline'])
        if item.get('adjustment')=='earlier_only': valid &= t <= preferred
        if item.get('adjustment')=='later_only': valid &= t >= preferred
        if item['hard_time'] and t != preferred and utc(req.get('confirmations',{}).get(mid,item['requested_time'])) != t:
            valid &= draft['confirmation_status']=='pending' and 'time_change' in draft['pending_items']
        checks['constraints:'+mid] = bool(valid)
        checks['actual_save:'+mid] = any(e['tool']=='save_draft' and e['observation'].get('ok') and e['observation']['draft']['draft_id']==draft['draft_id'] for e in events)
    for mid,flags in expected.get('pending',{}).items():
        d=next((d for d in drafts if d['material_id']==mid),{})
        checks['pending:'+mid] = set(flags) <= set(d.get('pending_items',[]))
    return {'passed':all(checks.values()), 'checks':checks,'actual_status':result['task_status'],'steps':result['steps']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['mock','live'],default='mock')
    parser.add_argument('--suite',choices=['development','validation'],default='development')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): parser.error('output must not exist; previous evidence is preserved')
    args.output.mkdir(parents=True)
    suite_path=ROOT/'data/evaluation'/f'{args.suite}.json'
    cases=read(suite_path)
    fingerprint=source_hash()
    summary={'mode':args.mode,'suite':args.suite,'source_sha256':fingerprint,
             'suite_sha256':hashlib.sha256(suite_path.read_bytes()).hexdigest(),
             'started_at':datetime.now(timezone.utc).isoformat(),'cases':[]}
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    for case in cases:
        assert source_hash()==fingerprint,'source changed during evaluation'
        folder=args.output/case['name']; folder.mkdir()
        data=folder/'data'; data.mkdir()
        for name in ('materials','rules','schedule'): write(data/f'{name}.json',read(ROOT/'data'/f'{name}.json'))
        schedule=read(data/'schedule.json')
        remove={(s['channel'],utc(s['time'])) for s in case.get('remove_slots',[])}
        schedule=[s for s in schedule if (s['channel'],utc(s['time'])) not in remove]
        schedule.extend(case.get('add_slots',[]))
        write(data/'schedule.json',schedule)
        write(folder/'input.json',case['request'])
        write(folder/'expectation.json',case['expected'])
        with tempfile.TemporaryDirectory(prefix='expanded-scheduler-') as tmp:
            command=[sys.executable,'-m','scheduling_agent','run','--mode',args.mode,'--input',str((folder/'input.json').resolve()),
                     '--data-dir',str(data.resolve()),'--db',str(Path(tmp)/'state.sqlite'),
                     '--out-dir',str(folder.resolve()),'--max-steps',str(case.get('max_steps',24))]
            if case.get('fail_tool'): command+=['--fail-tool',case['fail_tool']]
            run=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=1200)
        if not (folder/'output.json').exists(): raise RuntimeError(f"{case['name']}: no output, exit {run.returncode}")
        write(folder/'execution.json',{'mode':args.mode,'suite':args.suite,'exit_code':run.returncode,
                                     'source_sha256':fingerprint,'stderr':run.stderr})
        outcome=check_case(folder)
        write(folder/'verification.json',outcome)
        summary['cases'].append({'case':case['name'],**outcome})
        write(args.output/'summary.json',summary)
        print(f"{args.suite}/{args.mode}/{case['name']}: {'PASS' if outcome['passed'] else 'FAIL'} {outcome['actual_status']} steps={outcome['steps']}",flush=True)
    summary.update(finished_at=datetime.now(timezone.utc).isoformat(),all_passed=all(c['passed'] for c in summary['cases']))
    write(args.output/'summary.json',summary)
    return 0 if summary['all_passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
