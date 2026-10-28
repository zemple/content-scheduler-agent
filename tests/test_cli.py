import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_scheduler import ROOT, TIME, ALT


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.input = self.root / 'input.json'
        self.input.write_text(json.dumps({'request_id': 'cli', 'items': [
            {'material_id': 'M01', 'requested_time': ALT, 'hard_time': True}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *extra):
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
        return subprocess.run([sys.executable, '-m', 'scheduling_agent', 'run', '--input', str(self.input),
                               '--db', str(self.root / 'db.sqlite'), '--out-dir', str(self.root / 'out'),
                               '--env-file', str(self.root / 'no-env'), *extra],
                              env=env, cwd=ROOT, capture_output=True, text=True)

    def test_mock_cli_and_duplicate_across_processes(self):
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        r = json.loads(first.stdout)
        self.assertEqual(r['task_status'], 'completed')
        self.assertEqual(len((self.root / 'out' / 'trace.jsonl').read_text().splitlines()), 5)
        second = self.run_cli()
        r2 = json.loads(second.stdout)
        self.assertEqual(r['items'][0]['draft_id'], r2['items'][0]['draft_id'])
        self.assertTrue(r2['items'][0]['duplicate'])

    def test_failure_exit_and_artifacts(self):
        result = self.run_cli('--fail-tool', 'save_draft')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)['items'][0]['draft_status'], 'failed')
        self.assertTrue((self.root / 'out' / 'output.json').exists())

    def test_invalid_input_exits_cleanly(self):
        self.input.write_text('{}')
        result = self.run_cli()
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stdout)['error'], 'invalid_input_or_configuration')
        self.assertNotIn('Traceback', result.stderr)

    def test_business_needs_input_exit(self):
        obj = json.loads(self.input.read_text())
        obj['items'][0]['material_id'] = 'M02'
        self.input.write_text(json.dumps(obj))
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['task_status'], 'needs_input')

    def test_limit_exit(self):
        result = self.run_cli('--max-steps', '1')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)['termination'], 'step_limit')
