import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agy_client import GenerationSuperseded
from codex_private import CodexPrivate


class Pipe:
    def __init__(self):
        self.content = ''
        self.closed = False

    def write(self, text):
        self.content += text

    def close(self):
        self.closed = True


class Process:
    def __init__(self, result=0):
        self.stdin = Pipe()
        self.returncode = result
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


class CodexPrivateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = CodexPrivate(root=self.root)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def factory(self, result, item_type='agent_message', exit_code=0):
        captured = {}

        def create(args, **kwargs):
            captured['args'], captured['kwargs'] = args, kwargs
            process = Process(exit_code)
            captured['process'] = process
            output = Path(args[args.index('--output-last-message') + 1])
            if result is not None:
                output.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            kwargs['stdout'].write(json.dumps({'item': {'type': item_type}}) + '\n')
            kwargs['stdout'].flush()
            return process

        return captured, create

    def test_uses_strong_model_without_tools_or_shared_session(self):
        captured, create = self.factory({'reply': True, 'text': '已整理'})
        payload = {'recent_messages': [{'name': '朋友甲', 'text': '帮我修改材料'}]}
        with patch('codex_private.shutil.which', return_value='codex.exe'), \
                patch('codex_private.subprocess.Popen', side_effect=create):
            self.assertEqual(self.client.ask(payload), {'reply': True, 'text': '已整理'})
        args = captured['args']
        self.assertIn('--ephemeral', args)
        self.assertIn('--ignore-user-config', args)
        self.assertIn('gpt-6-astra', args)
        self.assertIn('model_reasoning_effort="ultra"', args)
        self.assertIn('web_search="disabled"', args)
        for flag in ('shell_tool', 'unified_exec', 'apps', 'hooks', 'multi_agent', 'memories', 'plugins'):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertIn('朋友甲', captured['process'].stdin.content)
        self.assertNotIn('朋友乙', captured['process'].stdin.content)
        self.assertTrue(Path(captured['kwargs']['cwd']).is_relative_to(self.root / '.runtime'))
        self.assertFalse(Path(captured['kwargs']['cwd']).exists())
        self.assertEqual(self.client.last_result['model'], 'gpt-6-astra')
        self.assertEqual(self.client.last_result['effort'], 'ultra')

    def test_unexpected_tool_event_is_not_accepted_as_a_reply(self):
        captured, create = self.factory({'reply': True, 'text': '已整理'}, 'command_execution')
        with patch('codex_private.shutil.which', return_value='codex.exe'), \
                patch('codex_private.subprocess.Popen', side_effect=create):
            with self.assertRaisesRegex(RuntimeError, '意外调用了工具'):
                self.client.ask({'text': '问题'})

    def test_nonzero_exit_or_invalid_output_is_not_a_success(self):
        for result, code in ((None, 1), ({'reply': 'yes', 'text': 'x'}, 0)):
            captured, create = self.factory(result, exit_code=code)
            with self.subTest(result=result), \
                    patch('codex_private.shutil.which', return_value='codex.exe'), \
                    patch('codex_private.subprocess.Popen', side_effect=create):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.client.ask({'text': '问题'})

    def test_cancel_stops_running_process(self):
        captured, create = self.factory(None, exit_code=None)
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) > 1

        with patch('codex_private.shutil.which', return_value='codex.exe'), \
                patch('codex_private.subprocess.Popen', side_effect=create):
            with self.assertRaises(GenerationSuperseded):
                self.client.ask({'text': '问题'}, cancel=cancel)
        self.assertTrue(captured['process'].terminated)

    def test_timeout_stops_running_process(self):
        captured, create = self.factory(None, exit_code=None)
        self.client.timeout = 0
        with patch('codex_private.shutil.which', return_value='codex.exe'), \
                patch('codex_private.subprocess.Popen', side_effect=create):
            with self.assertRaises(TimeoutError):
                self.client.ask({'text': '问题'})
        self.assertTrue(captured['process'].terminated)


if __name__ == '__main__':
    unittest.main()
