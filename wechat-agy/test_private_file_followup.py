"""Follow-up attachment routing against a real local private archive."""
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from private_chat import PrivateConversation
from private_file_host import FileCoordinator, _confirmed_source, _get, _put
from private_files import FileSendResult
from private_host import private_loop
from test_private_chat import Database, Model, row
from test_private_file_integration import Guard


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database([])
        self.contact = {'id': 'wxid_friend', 'name': '朋友', 'relationship': 'girlfriend'}
        self.chat = PrivateConversation(self.db, self.root, 'wxid_friend', '朋友',
            relationship='girlfriend', fast=Model({}), high=Model({}), strong=Model({}), guard=Guard())
        self.addCleanup(self.chat.close)
        self.chat.start()
        self.bot = SimpleNamespace(data=self.root / 'data', stop=threading.Event(),
                                   update_private_status=lambda *a, **kw: None)
        self.coord = FileCoordinator(self.bot, self.chat, self.contact)
        self.output = self.root / 'result.xlsx'
        self.output.write_text('fake file', encoding='utf-8')

    def message(self, seq, text, sender='wxid_friend', created=None, kind='文本'):
        item = row(seq, seq, sender, text)
        item['type'] = kind
        if created is not None:
            item['create_time'] = created
        self.db.rows.append(item)
        self.chat.poll()
        return self.chat.archive.recent(100)[-1]

    def historical_context(self):
        request = self.message(1, '检查一下毛利表')
        attachment = self.message(2, '<msg><appmsg><type>6</type><title>毛利表.xlsx</title></appmsg></msg>',
                                  kind='文件/链接/卡片')
        self.chat.ack([request, attachment])
        self.message(3, '毛利表有几处需要调整。', sender='wxid_bot')
        return request, attachment

    def drain(self, pending, deadline=3):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            normal, deferred, _ = self.coord.step(pending)
            pending = normal + deferred
            if not pending:
                break
            time.sleep(.01)
        return pending

    def test_acknowledged_file_is_linked_to_explicit_followup(self):
        _, attachment = self.historical_context()
        followup = self.message(4, '直接帮忙修改一下吗')
        pending = self.chat.pending()
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', return_value=self.output) as run, \
             patch('private_files.send_private_file', return_value=FileSendResult('confirmed', 'ok')) as send:
            self.assertEqual(self.drain(pending), [])
            send.assert_called_once()
            self.assertEqual(run.call_args.args[2]['local_id'], 2)
            self.assertIn('直接帮忙修改一下吗', run.call_args.args[3])
        self.assertEqual(self.chat.pending(), [])
        self.assertEqual(_get(self.chat, attachment['id'])['status'], 'confirmed')

    def test_reminder_between_file_request_and_followup_keeps_context(self):
        self.historical_context()
        reminder = self.message(4, '？肥鱼干活')
        self.chat.ack([reminder])
        self.message(5, '我在查看。', sender='wxid_bot')
        self.message(6, '直接帮忙修改一下吗')
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', return_value=self.output) as run, \
             patch('private_files.send_private_file', return_value=FileSendResult('confirmed', 'ok')):
            self.assertEqual(self.drain(self.chat.pending()), [])
            self.assertEqual(run.call_args.args[2]['local_id'], 2)
            self.assertIn('直接帮忙修改一下吗', run.call_args.args[3])

    def test_previous_confirmed_task_can_accept_a_distinct_followup_once(self):
        request, attachment = self.historical_context()
        _put(self.chat, attachment['id'], [request['id']], self.output, 'confirmed')
        self.message(4, '直接帮忙修改一下吗')
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', return_value=self.output) as run, \
             patch('private_files.send_private_file', return_value=FileSendResult('confirmed', 'ok')) as send:
            self.assertEqual(self.drain(self.chat.pending()), [])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs['source_override'], self.output)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(self.coord.step(self.chat.pending()), ([], [], False))
            self.assertEqual(send.call_count, 1)

    def test_missing_confirmed_output_does_not_fall_back_to_original(self):
        request, attachment = self.historical_context()
        _put(self.chat, attachment['id'], [request['id']], self.root / 'missing.xlsx', 'confirmed')
        followup = self.message(4, '直接帮忙修改一下吗')
        with patch('private_file_host.run_file_task') as run:
            normal, deferred, changed = self.coord.step(self.chat.pending())
            self.assertEqual(normal, [])
            self.assertEqual(deferred, [followup])
            self.assertFalse(changed)
            run.assert_not_called()

    def test_confirmed_source_survives_a_stale_followup_result(self):
        request, attachment = self.historical_context()
        _confirmed_source(self.chat, attachment['id'], self.output)
        _put(self.chat, attachment['id'], [], '', 'stale')
        self.message(4, '直接帮忙修改一下吗')
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', return_value=self.output) as run, \
             patch('private_files.send_private_file', return_value=FileSendResult('confirmed', 'ok')):
            self.assertEqual(self.drain(self.chat.pending()), [])
            self.assertEqual(run.call_args.kwargs['source_override'], self.output)

    def test_newest_relevant_attachment_is_used(self):
        self.historical_context()
        request = self.message(4, '请检查这个销售表')
        attachment = self.message(5, '<msg><appmsg><type>6</type><title>销售表.xlsx</title></appmsg></msg>',
                                  kind='文件/链接/卡片')
        self.chat.ack([request, attachment])
        self.message(6, '销售表有一处问题。', sender='wxid_bot')
        self.message(7, '直接帮忙修改一下吗')
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', return_value=self.output) as run, \
             patch('private_files.send_private_file', return_value=FileSendResult('confirmed', 'ok')):
            self.assertEqual(self.drain(self.chat.pending()), [])
            self.assertEqual(run.call_args.args[2]['local_id'], 5)

    def test_ineligible_context_does_not_start_file_work(self):
        for case in ('late', 'unrelated_topic', 'ordinary_text', 'other_contact'):
            with self.subTest(case=case):
                # Each case owns its own archive and queues.
                root = self.root / case
                db = Database([])
                chat = PrivateConversation(db, root, 'wxid_friend', '朋友',
                    relationship='girlfriend', fast=Model({}), high=Model({}), strong=Model({}), guard=Guard())
                try:
                    chat.start()
                    rows = [row(1, 1, 'wxid_friend', '检查一下毛利表'),
                            row(2, 2, 'wxid_friend', '<msg><appmsg><type>6</type><title>毛利表.xlsx</title></appmsg></msg>'),
                            row(3, 3, 'wxid_bot', '有两处需要调整')]
                    rows[1]['type'] = '文件/链接/卡片'
                    db.rows.extend(rows)
                    chat.poll()
                    chat.ack(chat.pending())
                    if case == 'unrelated_topic':
                        db.rows.append(row(4, 4, 'wxid_friend', '晚上吃什么'))
                        chat.poll()
                        chat.ack(chat.pending())
                    text = '晚饭吃什么' if case == 'ordinary_text' else '直接帮忙修改一下吗'
                    followup = row(5, 5, 'wxid_friend', text)
                    if case == 'late':
                        followup['create_time'] = rows[1]['create_time'] + 3600
                    db.rows.append(followup)
                    chat.poll()
                    contact = dict(self.contact)
                    if case == 'other_contact':
                        contact.pop('relationship')
                    coord = FileCoordinator(self.bot, chat, contact)
                    with patch('private_file_host.run_file_task') as run:
                        normal, deferred, changed = coord.step(chat.pending())
                        self.assertEqual([m['content'] for m in normal], [text])
                        self.assertEqual(deferred, [])
                        self.assertFalse(changed)
                        run.assert_not_called()
                finally:
                    chat.close()

    def test_pending_ambiguous_prior_send_is_not_retried(self):
        request, attachment = self.historical_context()
        _put(self.chat, attachment['id'], [request['id']], self.output, 'unconfirmed')
        self.message(4, '直接帮忙修改一下吗')
        with patch('private_file_host.run_file_task') as run, \
             patch('private_files.send_private_file') as send:
            self.coord.step(self.chat.pending())
            run.assert_not_called()
            send.assert_not_called()

    def test_political_followup_does_not_start_worker_or_send(self):
        self.historical_context()
        followup = self.message(4, '直接帮忙修改涉政内容吗')
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task') as run, \
             patch('private_files.send_private_file') as send:
            self.coord.step(self.chat.pending())
            if self.coord.job:
                self.assertTrue(self.coord.job['done'].wait(3))
            run.assert_not_called()
            send.assert_not_called()
            self.assertIn(followup['id'], [m['id'] for m in self.chat.pending()])

    def test_new_instruction_during_followup_discards_old_result(self):
        self.historical_context()
        self.message(4, '直接帮忙修改一下吗')
        started, release = threading.Event(), threading.Event()
        outputs = [self.root / 'first.xlsx', self.root / 'second.xlsx']
        for path in outputs:
            path.write_text('fake', encoding='utf-8')
        requests, submitted = [], []
        def work(db, contact, item, request, guard, root):
            requests.append(request)
            if len(requests) == 1:
                started.set()
                if not release.wait(3):
                    raise TimeoutError('测试未释放工作线程')
            return outputs[len(requests) - 1]
        def send(bot, chat, contact, path):
            submitted.append(path)
            return FileSendResult('confirmed', 'ok')
        with patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', side_effect=work), \
             patch('private_files.send_private_file', side_effect=send):
            self.coord.step(self.chat.pending())
            self.assertTrue(started.wait(3))
            self.message(5, '帮我改成蓝色')
            normal, deferred, _ = self.coord.step(self.chat.pending())
            self.assertEqual(normal, [], '后续修改要求不能作为普通聊天发送')
            self.assertEqual([m['content'] for m in deferred], ['直接帮忙修改一下吗', '帮我改成蓝色'])
            release.set()
            self.assertEqual(self.drain(self.chat.pending()), [])
            self.assertGreaterEqual(len(requests), 2)
            self.assertIn('帮我改成蓝色', requests[-1])
            self.assertEqual(submitted, [outputs[1]])

    def test_main_loop_starts_file_coordinator_for_historical_attachment(self):
        self.historical_context()
        (self.root / 'PERSONA.md').write_text('大肥鱼', encoding='utf-8')
        ready, submitted = threading.Event(), threading.Event()
        bot = SimpleNamespace(data=self.root / 'data', stop=threading.Event(),
            send_lock=threading.Lock(), bridge=SimpleNamespace(db=self.db),
            config={'poll_seconds': .01}, private_sessions={}, private_sessions_lock=threading.Lock(),
            update_private_status=lambda contact, **kw: ready.set() if kw.get('private_ready') else None)
        def send(*args):
            submitted.set()
            return FileSendResult('confirmed', 'ok')
        with patch('private_host.PrivateConversation', return_value=self.chat), \
             patch.object(self.chat, 'close', return_value=None), \
             patch('private_file_host.PoliticalGuard', side_effect=lambda **kwargs: Guard()), \
             patch('private_file_host.run_file_task', return_value=self.output), \
             patch('private_files.send_private_file', side_effect=send), \
             patch('private_host.send_private') as send_text:
            thread = threading.Thread(target=private_loop, args=(bot, self.contact), daemon=True)
            thread.start()
            try:
                self.assertTrue(ready.wait(3))
                self.db.rows.append(row(4, 4, 'wxid_friend', '直接帮忙修改一下吗'))
                self.assertTrue(submitted.wait(4), '历史附件追问没有进入文件任务')
                send_text.assert_not_called()
            finally:
                bot.stop.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
