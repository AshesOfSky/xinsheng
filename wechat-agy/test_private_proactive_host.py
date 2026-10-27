import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from private_host import PrivateTargetUnavailable, start_proactive, try_proactive


class ProactiveHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'PERSONA.md').write_text('大肥鱼', encoding='utf-8')
        self.bot = SimpleNamespace(data=root / 'data', stop=threading.Event(),
                                   send_lock=threading.Lock(), bridge=object(),
                                   update_private_status=Mock())
        self.chat = SimpleNamespace(poll=Mock(side_effect=[[], []]))
        self.contact = {'id': 'wxid_friend', 'name': '好友'}
        self.proactive = SimpleNamespace(eligible=Mock(return_value=True),
            generate=Mock(return_value='大肥鱼来打个招呼'), reserve=Mock(return_value=7), finish=Mock())

    def test_confirmed_send_is_recorded_once(self):
        with patch('private_host.send_private', return_value=(True, 'submitted')) as send, \
             patch('private_host.confirm_private', return_value=True):
            self.assertEqual(try_proactive(self.bot, self.chat, self.contact, self.proactive, '大肥鱼来打个招呼'), [])
        send.assert_called_once()
        self.proactive.finish.assert_called_once_with(7, 'confirmed')
        self.assertEqual(self.chat.poll.call_count, 2)

    def test_new_message_during_generation_takes_priority(self):
        incoming = {'id': 123}
        self.chat.poll.side_effect = [[incoming]]
        with patch('private_host.send_private') as send:
            self.assertEqual(try_proactive(self.bot, self.chat, self.contact, self.proactive, '大肥鱼来打个招呼'), [incoming])
        send.assert_not_called()
        self.proactive.reserve.assert_not_called()

    def test_ambiguous_send_is_not_retried(self):
        with patch('private_host.send_private', side_effect=RuntimeError('unknown')):
            with self.assertRaises(RuntimeError):
                try_proactive(self.bot, self.chat, self.contact, self.proactive, '大肥鱼来打个招呼')
        self.proactive.finish.assert_called_once_with(7, 'unconfirmed')

    def test_missing_window_is_cancelled_before_send(self):
        with patch('private_host.send_private', side_effect=PrivateTargetUnavailable('window')):
            self.assertEqual(try_proactive(self.bot, self.chat, self.contact, self.proactive, '大肥鱼来打个招呼'), [])
        self.proactive.finish.assert_called_once_with(7, 'cancelled')

    def test_generation_runs_in_background(self):
        started, release = threading.Event(), threading.Event()
        def generate(persona):
            started.set()
            release.wait(2)
            return '你好'
        self.proactive.generate.side_effect = generate
        job = start_proactive(self.bot, self.proactive)
        self.assertTrue(started.wait(1))
        self.assertFalse(job['done'].is_set())
        release.set()
        self.assertTrue(job['done'].wait(1))
        self.assertEqual(job['draft'], '你好')


if __name__ == '__main__':
    unittest.main()
