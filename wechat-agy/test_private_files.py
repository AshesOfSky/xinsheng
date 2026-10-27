import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from private_files import confirm_private_file, send_private_file
from private_host import file_send_hold, private_loop, send_private, PrivateTargetUnavailable


def node(name='', children=(), automation_id=''):
    return SimpleNamespace(Name=name, AutomationId=automation_id, GetChildren=lambda: list(children))


class PrivateFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'result.xlsx'
        self.path.write_bytes(b'file')
        self.contact = {'id': 'wxid_target', 'name': '朋友'}
        self.db = SimpleNamespace(account='account', get_messages=Mock(return_value=[]))
        self.chat = SimpleNamespace(db=self.db, contact_id='wxid_target', self_id='wxid_self')
        self.bot = SimpleNamespace(send_lock=threading.Lock(), data=Path(self.tmp.name) / 'data',
                                   bridge=SimpleNamespace(config={'account': 'account'}))
        self.children = []
        self.viewport = node(automation_id='chat_input_field.qt_scrollarea_viewport')
        self.viewport.GetChildren = lambda: list(self.children)
        self.edit = node(children=[self.viewport])
        self.uia = SimpleNamespace(_win=SimpleNamespace(AutomationId='ChatSingleWindowwxid_target'),
                                   _read_edit_value=lambda edit: '', current_chat=lambda: '朋友')
        self.gui = SimpleNamespace(_get_db=lambda: self.db, _get_uia=lambda: self.uia,
                                   copy_files_to_clipboard=Mock(return_value=True))

    def invoke(self, paste, submit):
        with patch('win32gui.FindWindow', return_value=123), \
             patch('wechatauto.guia.WeChatGUI', return_value=self.gui), \
             patch('private_files.chat_input', return_value=self.edit), \
             patch('private_files.focus_editor'), patch('private_files.send_keys', side_effect=paste), \
             patch('private_files.submit', side_effect=submit), patch('private_files.time.sleep'):
            return send_private_file(self.bot, self.chat, self.contact, self.path, timeout=0)

    def test_sends_under_shared_lock_only_with_exact_visible_file_draft(self):
        def paste(*args):
            self.assertTrue(self.bot.send_lock.locked())
            self.children.append(node('result.xlsx'))
        def submit(*args):
            self.assertTrue(self.bot.send_lock.locked())
            self.db.get_messages.return_value = [{
                'shard': 'one', 'local_id': 20, 'sender_username': 'wxid_self',
                'type': '文件/链接/卡片', 'create_time': __import__('time').time(),
                'content': '<msg><appmsg><type>6</type><title>result.xlsx</title></appmsg></msg>'}]
        result = self.invoke(paste, submit)
        self.assertEqual(result.status, 'confirmed')
        self.assertFalse((self.bot.data / 'private/wxid_target/file-send-pending').exists())

    def test_existing_draft_is_left_untouched(self):
        self.children.append(node('existing.xlsx'))
        paste, submit = Mock(), Mock()
        self.assertEqual(self.invoke(paste, submit).status, 'not_attempted')
        paste.assert_not_called()
        submit.assert_not_called()
        self.gui.copy_files_to_clipboard.assert_not_called()

    def test_unrecognized_paste_stops_before_click_and_is_not_retryable(self):
        submit = Mock()
        result = self.invoke(lambda *args: None, submit)
        self.assertEqual(result.status, 'unconfirmed')
        submit.assert_not_called()
        self.assertTrue((self.bot.data / 'private/wxid_target/file-send-pending').exists())
        self.assertEqual(self.invoke(Mock(), submit).status, 'not_attempted')

    def test_focus_loss_during_paste_is_unconfirmed(self):
        submit = Mock()
        def paste(*args):
            raise RuntimeError('焦点已丢失')
        self.assertEqual(self.invoke(paste, submit).status, 'unconfirmed')
        submit.assert_not_called()

    def test_wrong_private_window_never_touches_clipboard(self):
        self.uia._win.AutomationId = 'ChatSingleWindowother'
        paste, submit = Mock(), Mock()
        self.assertEqual(self.invoke(paste, submit).status, 'not_attempted')
        paste.assert_not_called()
        self.gui.copy_files_to_clipboard.assert_not_called()

    def test_confirmation_rejects_old_or_wrong_sender_or_link(self):
        def row(key, sender='wxid_self', typ='6'):
            return {'shard': 'one', 'local_id': key, 'sender_username': sender,
                    'type': '文件/链接/卡片', 'create_time': 100,
                    'content': f'<msg><appmsg><type>{typ}</type><title>result.xlsx</title></appmsg></msg>'}
        self.db.get_messages.return_value = [row(1), row(2, 'wxid_other'), row(3, typ='5')]
        self.assertFalse(confirm_private_file(self.chat, 'result.xlsx', {('one', 1)}, 100))
        self.db.get_messages.return_value.append(row(4))
        self.assertTrue(confirm_private_file(self.chat, 'result.xlsx', {('one', 1)}, 100))

    def test_pending_file_stops_only_its_contact_before_text_send(self):
        hold = file_send_hold(self.bot, self.contact)
        hold.parent.mkdir(parents=True)
        hold.touch()
        other = {'id': 'wxid_other', 'name': '另一个人'}
        self.assertFalse(file_send_hold(self.bot, other).exists())
        (self.bot.data.parent / 'PERSONA.md').write_text('persona', encoding='utf-8')
        incoming = {'id': 1, 'member_id': self.contact['id'], 'name': '朋友',
                    'is_self': False, 'is_at': False, 'kind': '文本', 'content': '你好'}
        polls = iter([[incoming], []])
        archive = SimpleNamespace(recent=lambda n: [])
        chat = SimpleNamespace(start=lambda: None, pending=lambda: [],
            poll=lambda: next(polls, []), last_model='test', archive=archive,
            guard=SimpleNamespace(filter=lambda *a, **k: (set(), False)),
            generate=lambda *a: {'reply': True, 'text': '你好'}, claim=Mock(), close=lambda: None)
        stop = threading.Event()
        bot = self.bot
        bot.stop, bot.bridge.db = stop, None
        bot.private_sessions, bot.private_sessions_lock = {}, threading.Lock()
        bot.config = {'poll_seconds': .01}
        bot.update_private_status = Mock()
        with patch('private_host.PrivateConversation', return_value=chat), \
             patch('private_host.send_private') as send, \
             patch.object(stop, 'wait', side_effect=lambda _: stop.set()):
            private_loop(bot, self.contact)
        send.assert_not_called()
        chat.claim.assert_not_called()
        self.assertTrue(hold.exists())
        self.assertTrue(any('文件发送状态待核' in str(c) for c in bot.update_private_status.call_args_list))

    def test_private_text_refuses_visible_attachment_or_unreadable_value(self):
        self.children.append(node('manual.xlsx'))
        with patch('win32gui.FindWindow', return_value=123), \
             patch('wechatauto.guia.WeChatGUI', return_value=self.gui), \
             patch('private_host.chat_input', return_value=self.edit), \
             patch('private_host.send_text') as send:
            with self.assertRaises(PrivateTargetUnavailable):
                send_private(self.bot.bridge, self.contact, '你好')
            self.children.clear()
            self.uia._read_edit_value = lambda _: None
            with self.assertRaises(PrivateTargetUnavailable):
                send_private(self.bot.bridge, self.contact, '你好')
            send.assert_not_called()
            self.uia._read_edit_value = lambda _: ''
            self.viewport.GetChildren = Mock(side_effect=RuntimeError('读取失败'))
            with self.assertRaises(PrivateTargetUnavailable):
                send_private(self.bot.bridge, self.contact, '你好')
            send.assert_not_called()
            self.viewport.GetChildren = lambda: []
            send.return_value = (True, 'ok')
            self.assertEqual(send_private(self.bot.bridge, self.contact, '你好'), (True, 'ok'))
            send.assert_called_once_with(self.uia, '你好', '朋友')


if __name__ == '__main__':
    unittest.main()
