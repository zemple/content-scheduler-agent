#!/usr/bin/env python3
"""Execute actual CLI cases and validate factual outputs. Never fabricates traces."""
import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = '2026-11-01T18:00:00+08:00'
A = '2026-11-01T19:00:00+08:00'

CASES = {
    'normal': {'time': A, 'status': 'completed', 'saved': 1},
    'missing': {'mid': 'M02', 'status': 'needs_input', 'saved': 0},
    'risk': {'mid': 'M03', 'status': 'blocked', 'saved': 0},
    'conflict': {'status': 'awaiting_confirmation', 'saved': 1, 'suggestion': '2026-11-01T11:00:00+00:00'},
    'duplicate': {'time': A, 'status': 'completed', 'saved': 1, 'repeat': True},
    'save_failure': {'fail': 'save_draft', 'status': 'incomplete', 'saved': 0},
    'tool_failure': {'fail': 'read_material', 'status': 'incomplete', 'saved': 0},
    'changed_occupancy': {'occupied_extra': A, 'status': 'awaiting_confirmation', 'saved': 1, 'suggestion': '2026-11-01T12:00:00+00:00'},
    'confirmed': {'confirmations': {'M01': A}, 'status': 'completed', 'saved': 1, 'suggestion': '2026-11-01T11:00:00+00:00'},
    'manual_review': {'mid': 'M04', 'time': A, 'status': 'awaiting_confirmation', 'saved': 1},
    'no_slots': {'no_slots': True, 'status': 'incomplete', 'saved': 0},
    'step_limit': {'max_steps': 1, 'status': 'incomplete', 'saved': 0},
    'batch': {'batch': True, 'max_steps': 20, 'status': 'partial', 'saved': 1},
    'repeat_changed_occupancy': {'time': A, 'status': 'completed', 'second_status': 'incomplete', 'saved': 1, 'repeat': True, 'occupy_after_first': True},
    'repeat_changed_rules': {'time': A, 'status': 'completed', 'second_status': 'blocked', 'saved': 1, 'repeat': True, 'rules_after_first': True},
    'batch_pending': {'batch_pending': True, 'max_steps': 20, 'status': 'awaiting_confirmation', 'saved': 2},
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['mock', 'live'], default='mock')
    parser.add_argument('--output', type=Path, default=ROOT / 'runs' / 'reproduced')
    parser.add_argument('--cases', default=','.join(CASES))
    args = parser.parse_args()
    chosen = args.cases.split(',')
    if set(chosen) - CASES.keys():
        parser.error('unknown case')
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be a fresh directory to preserve previous evidence')
    output.mkdir(parents=True)
    summary = {'mode': args.mode, 'started_at': datetime.now(timezone.utc).isoformat(), 'cases': []}
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
    for name in chosen:
        spec = CASES[name]
        folder = output / name
        folder.mkdir()
        world_dir = folder / 'data'
        world_dir.mkdir()
        for source in (ROOT / 'data').glob('*.json'):
            (world_dir / source.name).write_bytes(source.read_bytes())
        schedule = json.loads((world_dir / 'schedule.json').read_text())
        if spec.get('occupied_extra'):
            schedule.append({'channel': 'video', 'time': spec['occupied_extra']})
        if spec.get('no_slots'):
            # At least the complete six-slot search window is occupied.
            schedule = [{'channel': 'video', 'time': (datetime.fromisoformat(T) + timedelta(hours=i)).isoformat()} for i in range(24)]
        write_json(world_dir / 'schedule.json', schedule)
        request = {'request_id': name + '-001', 'items': [
            {'material_id': spec.get('mid', 'M01'), 'requested_time': spec.get('time', T), 'hard_time': True}],
            'confirmations': spec.get('confirmations', {})}
        if spec.get('batch'):
            request['items'][0]['requested_time'] = A
            request['items'].append({'material_id': 'M03', 'requested_time': T, 'hard_time': True})
        if spec.get('batch_pending'):
            request['items'].append({'material_id': 'M05', 'requested_time': T, 'hard_time': True})
        write_json(folder / 'input.json', request)
        runs, checks = [], []
        with tempfile.TemporaryDirectory(prefix='scheduler-eval-') as tmp:
            for iteration in range(2 if spec.get('repeat') else 1):
                if iteration and spec.get('occupy_after_first'):
                    schedule.append({'channel': 'video', 'time': A})
                    write_json(world_dir / 'schedule.json', schedule)
                if iteration and spec.get('rules_after_first'):
                    materials = json.loads((world_dir / 'materials.json').read_text())
                    materials['M01']['claims'] = ['效率提升80%']
                    write_json(world_dir / 'materials.json', materials)
                dest = folder / ('first' if iteration == 0 else 'second') if spec.get('repeat') else folder
                iteration_world = world_dir
                if spec.get('repeat'):
                    dest.mkdir(parents=True, exist_ok=True)
                    iteration_world = dest / 'data'
                    iteration_world.mkdir()
                    for source in world_dir.glob('*.json'):
                        (iteration_world / source.name).write_bytes(source.read_bytes())
                command = [sys.executable, '-m', 'scheduling_agent', 'run', '--mode', args.mode,
                           '--input', str(folder / 'input.json'), '--data-dir', str(iteration_world),
                           '--db', str(Path(tmp) / 'state.sqlite'), '--out-dir', str(dest),
                           '--max-steps', str(spec.get('max_steps', 12))]
                if spec.get('fail'):
                    command += ['--fail-tool', spec['fail']]
                run = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=900)
                if not (dest / 'output.json').exists():
                    raise RuntimeError(f'{name}: no output artifact; exit={run.returncode}')
                result = json.loads((dest / 'output.json').read_text())
                drafts = json.loads((dest / 'drafts.json').read_text())
                trace = [json.loads(line) for line in (dest / 'trace.jsonl').read_text().splitlines()]
                write_json(dest / 'execution.json', {'exit_code': run.returncode, 'mode': args.mode,
                           'max_steps': spec.get('max_steps', 12), 'injected_failure': spec.get('fail'),
                           'input': 'input.json', 'data': 'data/', 'database_rows': len(drafts),
                           'stderr': run.stderr, 'model_response_ids': [e['model_metadata'].get('response_id') for e in trace if e['model_metadata'].get('response_id')]})
                expected = spec.get('second_status', spec['status']) if iteration else spec['status']
                checks += [result['task_status'] == expected, len(drafts) == spec['saved'],
                           all(i['publication_status'] == 'not_published' for i in result['items']),
                           all(e['next_action'] for e in trace)]
                if args.mode == 'live':
                    checks.append(any(e['model_metadata'].get('response_id') for e in trace))
                if spec.get('suggestion'):
                    checks.append(result['items'][0]['suggested_time'] == spec['suggestion'])
                if spec.get('batch_pending'):
                    checks.append(len({(d['channel'], d['suggested_time']) for d in drafts}) == 2)
                if spec.get('repeat') and iteration == 1:
                    checks += [drafts[0]['draft_id'] == runs[0]['items'][0]['draft_id']]
                    if spec.get('second_status'):
                        checks.append(result['items'][0]['confirmation_status'] == 'needs_revalidation')
                    else:
                        checks.append(result['items'][0]['duplicate'])
                runs.append(result)
        passed = all(checks)
        row = {'case': name, 'passed': passed, 'expected_status': spec.get('second_status', spec['status']),
               'actual_status': runs[-1]['task_status'], 'steps': [r['steps'] for r in runs],
               'artifacts': name + '/', 'checks_passed': sum(checks), 'checks_total': len(checks)}
        summary['cases'].append(row)
        write_json(output / 'summary.json', summary)
        print(f"{args.mode}/{name}: {'PASS' if passed else 'FAIL'} status={row['actual_status']} steps={row['steps']}", flush=True)
    summary['finished_at'] = datetime.now(timezone.utc).isoformat()
    summary['all_passed'] = all(c['passed'] for c in summary['cases'])
    write_json(output / 'summary.json', summary)
    return 0 if summary['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
