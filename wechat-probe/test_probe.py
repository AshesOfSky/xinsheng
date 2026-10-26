import unittest
from types import SimpleNamespace
from probe import event_record


class ProbeTests(unittest.TestCase):
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
