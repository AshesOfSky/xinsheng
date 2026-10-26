import unittest
import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from probe import emit, event_record


class ProbeTests(unittest.TestCase):
    def test_worker_json_round_trips_through_gbk_stdout(self):
        messages = ['普通中文消息', '@测试小号\u2005你好😀\n第二行']
        expected = [event_record(SimpleNamespace(
            group='中文测试群', content=content, timestamp=1,
            group_nickname='测试小号', is_at_me=index == 1))
            for index, content in enumerate(messages)]
        raw = io.BytesIO()
        with io.TextIOWrapper(raw, encoding='gbk', errors='strict') as stdout:
            with redirect_stdout(stdout):
                for record in expected:
                    emit(record)
            wire = raw.getvalue()
        self.assertTrue(wire.isascii())
        lines = wire.decode('utf-8').splitlines()
        self.assertEqual(len(lines), len(expected))
        self.assertEqual([json.loads(line) for line in lines], expected)
        self.assertNotIn('\ufffd', ''.join(lines))

    def test_does_not_invent_sender_or_self_status(self):
        event = SimpleNamespace(group='测试群', content='@小号 你好', timestamp=1,
                                group_nickname='小号', is_at_me=True)
        record = event_record(event)
        self.assertIsNone(record['sender'])
        self.assertEqual(record['manual_self_detection'], 'unsupported')
        self.assertTrue(record['is_at_me'])

    def test_real_handler_never_creates_reply_action(self):
        from wx4py import CallbackHandler
        event = SimpleNamespace(group='测试群', content='你好', timestamp=1,
                                group_nickname='小号', is_at_me=False)
        collected = []
        def receive(event):
            collected.append(event_record(event))
        handler = CallbackHandler(receive, auto_reply=False)
        self.assertIsNone(handler.handle(event))
        self.assertEqual(len(collected), 1)


if __name__ == '__main__':
    unittest.main()
