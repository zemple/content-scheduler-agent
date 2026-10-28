"""Run from repository root: python -m scheduling_agent run --input FILE."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from .agent import Agent
from .domain import Request, ValidationError
from .models import ChatModel, MockModel, ModelError
from .store import Store
from .tools import PARAMS, Tools, load_world


def load_env(path):
    allowed = {'SCHEDULER_API_KEY', 'SCHEDULER_MODEL', 'SCHEDULER_BASE_URL', 'SCHEDULER_TIMEOUT'}
    if Path(path).exists():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if sep and key.strip() in allowed:
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv=None):
    parser = argparse.ArgumentParser(description='审核与排期草稿 Agent；不执行发布。')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--input', required=True, type=Path)
    run.add_argument('--mode', choices=['mock', 'live'], default='mock')
    run.add_argument('--data-dir', type=Path, default=Path('data'))
    run.add_argument('--db', type=Path, default=Path('runs/state.sqlite'))
    run.add_argument('--out-dir', type=Path, default=Path('runs/latest'))
    run.add_argument('--env-file', type=Path, default=Path('.env'))
    run.add_argument('--max-steps', type=int, default=12)
    run.add_argument('--fail-tool', choices=list(PARAMS))
    run.add_argument('--fail-count', type=int, default=-1, help='-1=persistent, positive=number of failed calls')
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--db', type=Path, default=Path('runs/state.sqlite'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            print(json.dumps(Store(args.db).drafts(), ensure_ascii=False, indent=2))
            return 0
        load_env(args.env_file)
        request = Request.parse(json.loads(args.input.read_text(encoding='utf-8')))
        world = load_world(args.data_dir)
        if args.mode == 'mock':
            model = MockModel()
        else:
            model = ChatModel(os.environ.get('SCHEDULER_API_KEY', ''), os.environ.get('SCHEDULER_MODEL', ''),
                              os.environ.get('SCHEDULER_BASE_URL', 'https://api.openai.com/v1'),
                              timeout=float(os.environ.get('SCHEDULER_TIMEOUT', '45')))
        if args.fail_count == 0 or args.fail_count < -1:
            raise ValidationError('fail-count must be -1 or a positive integer')
        tools = Tools(request, world, Store(args.db), {args.fail_tool: args.fail_count} if args.fail_tool else None)
        result, events = Agent(tools, model, args.max_steps).run()
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / 'input.json').write_text(json.dumps(request.as_dict(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (args.out_dir / 'output.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (args.out_dir / 'trace.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events), encoding='utf-8')
        (args.out_dir / 'drafts.json').write_text(json.dumps(tools.store.drafts(request.request_id), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['task_status'] in ('completed', 'awaiting_confirmation'):
            return 0
        return 3 if result['task_status'] in ('incomplete', 'partial') else 2
    except (ValidationError, ModelError, OSError, ValueError, sqlite3.Error) as exc:
        # API failures are represented by safe symbolic codes, never HTTP bodies or credentials.
        print(json.dumps({'task_status': 'incomplete', 'error': 'invalid_input_or_configuration',
                          'message': str(exc)}, ensure_ascii=False))
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
