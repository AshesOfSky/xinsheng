import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from private_host import confirm_private, private_loop, send_private, PrivateTargetUnavailable


def message(i, contact, text='你好'):
    return {'id': i, 'member_id': contact['id'], 'name': contact['name'],
            'is_self': False, 'is_at': False, 'kind': '文本', 'content': text}


def allow_guard():
    return SimpleNamespace(filter=lambda batch, context, draft='': (set(), False))


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
        incoming = message(1, contact)
        statuses = []
        archive = SimpleNamespace(recent=lambda n: [], record_outgoing=lambda *args: 42,
                                  outgoing_status=lambda *args: statuses.append(args))
        polls = iter([[incoming], []])
        chat = SimpleNamespace(start=lambda: None, poll=lambda: next(polls, []), last_model='test-model',
                               pending=lambda: [], claim=lambda batch: None,
                               release=lambda batch: None, ack=lambda batch: None,
                               generate=lambda *args: {'reply': True, 'text': '你好'},
                               archive=archive, guard=allow_guard(), close=lambda: None)
        bot = SimpleNamespace(stop=stop, send_lock=lock, bridge=SimpleNamespace(db=None),
                              data=__import__('pathlib').Path(__file__).parent / 'data',
                              config={'poll_seconds': 0.01}, private_sessions={},
                              private_sessions_lock=threading.Lock(), update_private_status=lambda contact_id, **kw: None)

        def send(bridge, target, text):
            self.assertTrue(lock.locked())
            self.assertEqual((target, text), (contact, '你好'))
            return True, ''

        def confirm(*args):
            stop.set()
            return True

        contact['relationship'] = 'girlfriend'
        with patch('private_host.PrivateConversation', return_value=chat) as create, \
                patch('private_host.send_private', side_effect=send), \
                patch('private_host.confirm_private', side_effect=confirm):
            private_loop(bot, contact)
        self.assertEqual(create.call_args.kwargs['relationship'], 'girlfriend')
        self.assertEqual(statuses, [(42, 'confirmed')])
        self.assertEqual(bot.private_sessions, {})

    def test_closed_window_keeps_batch_and_reuses_generated_reply(self):
        stop = threading.Event()
        contact = {'id': 'wxid_target', 'name': '朋友'}
        incoming = message(1, contact)
        statuses, transitions, generations = [], [], []
        archive = SimpleNamespace(recent=lambda n: [], record_outgoing=lambda *args: 42,
                                  outgoing_status=lambda *args: statuses.append(args))
        polls = iter([[incoming], [], []])
        chat = SimpleNamespace(start=lambda: None, pending=lambda: [], poll=lambda: next(polls, []), last_model='test-model',
            claim=lambda batch: transitions.append('claim'), release=lambda batch: transitions.append('release'),
            ack=lambda batch: transitions.append('ack'), archive=archive, guard=allow_guard(), close=lambda: None,
            generate=lambda *args: (generations.append(1) or {'reply': True, 'text': '你好'}))
        bot = SimpleNamespace(stop=stop, send_lock=threading.Lock(), bridge=SimpleNamespace(db=None),
            data=__import__('pathlib').Path(__file__).parent / 'data', config={'poll_seconds': 0.01},
            private_sessions={}, private_sessions_lock=threading.Lock(), update_private_status=lambda contact_id, **kw: None)

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

    def test_new_instructions_during_generation_replace_old_draft(self):
        stop = threading.Event()
        contact = {'id': 'wxid_target', 'name': '朋友'}
        first = message(1, contact, '原问题')
        second = message(2, contact, '新要求')
        polls = iter([[first], [second], [], []])
        drafts = iter([{'reply':True, 'text':'旧稿'}, {'reply':True, 'text':'新要求的回复'}])
        batches, sent = [], []
        archive = SimpleNamespace(recent=lambda n: [], record_outgoing=lambda *args: 9, outgoing_status=lambda *args: None)
        def generate(batch, persona):
            batches.append([m['id'] for m in batch])
            return next(drafts)
        chat = SimpleNamespace(start=lambda:None, pending=lambda:[], poll=lambda:next(polls, []), last_model='test-model',
            claim=lambda batch:None, release=lambda batch:None, ack=lambda batch:None,
            generate=generate, archive=archive, guard=allow_guard(), close=lambda:None)
        bot = SimpleNamespace(stop=stop, send_lock=threading.Lock(), bridge=SimpleNamespace(db=None),
            data=__import__('pathlib').Path(__file__).parent / 'data', config={'poll_seconds':0.01},
            private_sessions={}, private_sessions_lock=threading.Lock(), update_private_status=lambda contact_id, **kw:None)
        def send(bridge, target, text):
            sent.append(text)
            return True, ''
        def confirm(*args):
            stop.set()
            return True
        with patch('private_host.PrivateConversation', return_value=chat), \
                patch('private_host.send_private', side_effect=send), \
                patch('private_host.confirm_private', side_effect=confirm):
            private_loop(bot, contact)
        self.assertEqual(batches, [[1], [1, 2]])
        self.assertEqual(sent, ['新要求的回复'])

    def test_political_input_and_political_draft_are_silent(self):
        for block_at in ('input', 'draft'):
            with self.subTest(block_at=block_at):
                stop = threading.Event()
                contact = {'id': 'wxid_target', 'name': '朋友'}
                incoming = message(1, contact, '这件事怎么看')
                polls = iter([[incoming], []])
                calls, acknowledged = [], []
                def guard(batch, context, draft=''):
                    calls.append(draft)
                    if block_at == 'input':
                        return {1}, False
                    return (set(), bool(draft))
                def ack(batch):
                    acknowledged.extend(m['id'] for m in batch)
                    stop.set()
                archive = SimpleNamespace(recent=lambda n: [], record_outgoing=lambda *args: 1,
                                          outgoing_status=lambda *args: None)
                chat = SimpleNamespace(start=lambda:None, pending=lambda:[], poll=lambda:next(polls, []),
                    last_model='test-model', claim=lambda batch:None, release=lambda batch:None, ack=ack,
                    generate=lambda *args:{'reply':True, 'text':'待审核的草稿'}, archive=archive,
                    guard=SimpleNamespace(filter=guard), close=lambda:None)
                bot = SimpleNamespace(stop=stop, send_lock=threading.Lock(), bridge=SimpleNamespace(db=None),
                    data=__import__('pathlib').Path(__file__).parent / 'data', config={'poll_seconds':0.01},
                    private_sessions={}, private_sessions_lock=threading.Lock(), update_private_status=lambda contact_id, **kw:None)
                with patch('private_host.PrivateConversation', return_value=chat), \
                        patch('private_host.send_private') as send:
                    private_loop(bot, contact)
                send.assert_not_called()
                self.assertEqual(acknowledged, [1])
                self.assertEqual(len(calls), 1 if block_at == 'input' else 2)

    def test_mixed_private_batch_keeps_the_unrelated_message(self):
        stop = threading.Event()
        contact = {'id':'wxid_target','name':'朋友'}
        political = message(1, contact, '政治话题')
        ordinary = message(2, contact, '晚饭吃什么')
        polls = iter([[political, ordinary], []])
        acknowledged, batches, sent = [], [], []
        def guard(batch, context, draft=''):
            return ({m['id'] for m in batch if m['id'] == 1}, False)
        def generate(batch, persona):
            batches.append([m['id'] for m in batch])
            return {'reply':True, 'text':'吃火锅怎么样'}
        def ack(batch):
            acknowledged.extend(m['id'] for m in batch)
        archive = SimpleNamespace(recent=lambda n:[], record_outgoing=lambda *args:1,
                                  outgoing_status=lambda *args:None)
        chat = SimpleNamespace(start=lambda:None, pending=lambda:[], poll=lambda:next(polls, []),
            last_model='test-model', claim=lambda batch:None, release=lambda batch:None, ack=ack,
            generate=generate, archive=archive, guard=SimpleNamespace(filter=guard), close=lambda:None)
        bot = SimpleNamespace(stop=stop, send_lock=threading.Lock(), bridge=SimpleNamespace(db=None),
            data=__import__('pathlib').Path(__file__).parent/'data', config={'poll_seconds':0.01},
            private_sessions={}, private_sessions_lock=threading.Lock(), update_private_status=lambda contact_id, **kw:None)
        def send(bridge, target, text):
            sent.append(text)
            return True, ''
        def confirm(*args):
            stop.set()
            return True
        with patch('private_host.PrivateConversation', return_value=chat), \
                patch('private_host.send_private', side_effect=send), \
                patch('private_host.confirm_private', side_effect=confirm):
            private_loop(bot, contact)
        self.assertEqual(batches, [[2]])
        self.assertEqual(sent, ['吃火锅怎么样'])
        self.assertEqual(acknowledged, [1,2])

    def test_private_policy_unavailable_does_not_send_or_ack(self):
        stop = threading.Event()
        contact = {'id':'wxid_target','name':'朋友'}
        incoming = message(1, contact, '待判断内容')
        def guard(*args, **kwargs):
            raise RuntimeError('判断暂不可用')
        def status(**kw):
            if kw.get('private_error'):
                stop.set()
        archive = SimpleNamespace(recent=lambda n:[])
        chat = SimpleNamespace(start=lambda:None, pending=lambda:[], poll=lambda:[incoming],
            last_model=None, ack=lambda batch:None, archive=archive,
            guard=SimpleNamespace(filter=guard), close=lambda:None)
        bot = SimpleNamespace(stop=stop, send_lock=threading.Lock(), bridge=SimpleNamespace(db=None),
            data=__import__('pathlib').Path(__file__).parent/'data', config={'poll_seconds':0.01},
            private_sessions={}, private_sessions_lock=threading.Lock(), update_private_status=lambda contact_id, **kw: status(**kw))
        with patch('private_host.PrivateConversation', return_value=chat), \
                patch.object(chat, 'ack') as ack, patch('private_host.send_private') as send:
            private_loop(bot, contact)
        ack.assert_not_called()
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
