import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from private_host import confirm_private, private_loop, send_private, PrivateTargetUnavailable


class PrivateHostTests(unittest.TestCase):
    def test_send_checks_window_wxid_before_writing(self):
        contact = {'id': 'wxid_target', 'name': '朋友'}
        bridge = SimpleNamespace(config={'account': 'account'})
        wrong = SimpleNamespace(_win=SimpleNamespace(AutomationId='ChatSingleWindowwxid_other'))
        gui = SimpleNamespace(_get_db=lambda: SimpleNamespace(account='account'), _get_uia=lambda: wrong)
        with patch('win32gui.FindWindow', return_value=123), \
                patch('wechatauto.guia.WeChatGUI', return_value=gui), \
                patch('private_host.send_text') as send:
            with self.assertRaises(PrivateTargetUnavailable):
                send_private(bridge, contact, '你好')
            send.assert_not_called()

    def test_confirmation_requires_target_self_text_and_recent_time(self):
        rows = [{'sender_username': 'wxid_other', 'type': '文本', 'content': '你好', 'create_time': 100},
                {'sender_username': 'wxid_self', 'type': '文本', 'content': '你好', 'create_time': 80}]
        chat = SimpleNamespace(contact_id='wxid_target', self_id='wxid_self',
                               db=SimpleNamespace(get_messages=lambda *a, **k: rows))
        self.assertFalse(confirm_private(chat, '你好', 100))
        rows.append({'sender_username': 'wxid_self', 'type': '文本', 'content': '你好', 'create_time': 100})
        self.assertTrue(confirm_private(chat, '你好', 100))

    def test_input_preparation_error_can_be_retried_without_sending(self):
        contact = {'id': 'wxid_target', 'name': '朋友'}
        bridge = SimpleNamespace(config={'account': 'account'})
        with patch('win32gui.FindWindow', return_value=123), \
                patch('wechatauto.guia.WeChatGUI', side_effect=RuntimeError('窗口暂不可用')), \
                patch('private_host.send_text') as send:
            with self.assertRaises(PrivateTargetUnavailable):
                send_private(bridge, contact, '你好')
            send.assert_not_called()

    def test_private_reply_uses_shared_send_lock_and_confirms(self):
        stop = threading.Event()
        lock = threading.Lock()
        contact = {'id': 'wxid_target', 'name': '朋友'}
        incoming = {'id': 1, 'member_id': contact['id'], 'is_self': False, 'kind': '文本'}
        statuses = []
        archive = SimpleNamespace(record_outgoing=lambda *args: 42,
                                  outgoing_status=lambda *args: statuses.append(args))
        chat = SimpleNamespace(start=lambda: None, poll=lambda: [incoming],
                               pending=lambda: [], claim=lambda batch: None,
                               release=lambda batch: None, ack=lambda batch: None,
                               generate=lambda *args: {'reply': True, 'text': '你好'},
                               archive=archive, close=lambda: None)
        bot = SimpleNamespace(stop=stop, send_lock=lock, bridge=SimpleNamespace(db=None),
                              data=__import__('pathlib').Path(__file__).parent / 'data',
                              config={'poll_seconds': 0.01}, private_sessions={},
                              private_sessions_lock=threading.Lock(), update_status=lambda **kw: None)

        def send(bridge, target, text):
            self.assertTrue(lock.locked())
            self.assertEqual((target, text), (contact, '你好'))
            return True, ''

        def confirm(*args):
            stop.set()
            return True

        with patch('private_host.PrivateConversation', return_value=chat), \
                patch('private_host.send_private', side_effect=send), \
                patch('private_host.confirm_private', side_effect=confirm):
            private_loop(bot, contact)
        self.assertEqual(statuses, [(42, 'confirmed')])
        self.assertEqual(bot.private_sessions, {})

    def test_closed_window_keeps_batch_and_reuses_generated_reply(self):
        stop = threading.Event()
        contact = {'id': 'wxid_target', 'name': '朋友'}
        incoming = {'id': 1, 'member_id': contact['id'], 'is_self': False, 'kind': '文本'}
        statuses, transitions, generations = [], [], []
        archive = SimpleNamespace(record_outgoing=lambda *args: 42,
                                  outgoing_status=lambda *args: statuses.append(args))
        polls = iter([[incoming], []])
        chat = SimpleNamespace(start=lambda: None, pending=lambda: [], poll=lambda: next(polls),
            claim=lambda batch: transitions.append('claim'), release=lambda batch: transitions.append('release'),
            ack=lambda batch: transitions.append('ack'), archive=archive, close=lambda: None,
            generate=lambda *args: (generations.append(1) or {'reply': True, 'text': '你好'}))
        bot = SimpleNamespace(stop=stop, send_lock=threading.Lock(), bridge=SimpleNamespace(db=None),
            data=__import__('pathlib').Path(__file__).parent / 'data', config={'poll_seconds': 0.01},
            private_sessions={}, private_sessions_lock=threading.Lock(), update_status=lambda **kw: None)

        def confirm(*args):
            stop.set()
            return True

        with patch('private_host.PrivateConversation', return_value=chat), \
                patch('private_host.send_private', side_effect=[PrivateTargetUnavailable('closed'), (True, '')]), \
                patch('private_host.confirm_private', side_effect=confirm), \
                patch.object(stop, 'wait', return_value=False):
            private_loop(bot, contact)
        self.assertEqual(generations, [1])
        self.assertEqual(transitions, ['claim', 'release', 'claim', 'ack'])
        self.assertEqual(statuses, [(42, 'cancelled'), (42, 'confirmed')])


if __name__ == '__main__':
    unittest.main()
