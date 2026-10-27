import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from private_chat import PrivateConversation
from private_proactive import PrivateProactive, IDLE_SECONDS, INTERVAL_SECONDS
from test_private_chat import Database, Model


class Guard:
    def __init__(self):
        self.calls = []
        self.block = False
        self.block_draft = False

    def filter(self, targets, context, draft=''):
        self.calls.append((targets, context, draft))
        return ({m['id'] for m in targets} if self.block else set(), bool(draft and self.block_draft))

    def close(self):
        pass


class PrivateProactiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fast = Model({'reply': True, 'text': '大肥鱼来冒个泡，今天有没有遇到什么有趣的事？'})
        self.guard = Guard()
        self.chat = PrivateConversation(Database([]), self.root, 'wxid_friend', '朋友',
            relationship='girlfriend', fast=self.fast, high=Model({}), strong=Model({}), guard=self.guard)
        self.addCleanup(self.chat.close)
        self.chat.start()
        self.now = int(datetime(2026, 10, 27, 14, 0).timestamp())
        self.pr = PrivateProactive(self.chat, fast=self.fast, guard=self.guard)
        self.addCleanup(self.pr.close)
        self.counter = 0
        self.add('wxid_friend', '今天很忙', self.now - IDLE_SECONDS - 10)

    def add(self, sender, text, stamp, kind='文本'):
        self.counter += 1
        return self.chat.archive.add({'source_key': f'shard/{self.counter}', 'member_id': sender,
            'name': '朋友' if sender == 'wxid_friend' else '大肥鱼', 'created': stamp,
            'kind': kind, 'content': text, 'is_self': sender == 'wxid_bot', 'is_at': False,
            'sort_seq': self.counter})

    def test_person_and_quiet_hours(self):
        self.assertTrue(self.pr.eligible(self.now))
        self.chat.relationship = None
        self.assertFalse(self.pr.eligible(self.now))
        self.chat.relationship = 'girlfriend'
        for hour in (0, 8, 23):
            self.assertFalse(self.pr.eligible(int(datetime(2026, 10, 27, hour, 0).timestamp())))

    def test_default_sessions_are_independent_from_normal_private_replies(self):
        separate = PrivateProactive(self.chat)
        try:
            self.assertIsNot(separate.fast, self.chat.fast)
            self.assertIsNot(separate.guard, self.chat.guard)
            self.assertEqual(separate.fast.timeout, 15)
            self.assertEqual(separate.guard.session.timeout, 15)
        finally:
            separate.close()
        self.assertFalse(self.chat.fast.closed)

    def test_shutdown_between_guard_and_model_never_starts_model_or_defers(self):
        entered, release = threading.Event(), threading.Event()

        class BlockingGuard(Guard):
            def filter(self, targets, context, draft=''):
                entered.set()
                release.wait(3)
                return set(), False

        guard = BlockingGuard()
        model = Model({'reply': True, 'text': '嗨'})
        separate = PrivateProactive(self.chat, fast=model, guard=guard)
        job = threading.Thread(target=lambda: separate.generate('鱼的人格'))
        job.start()
        self.assertTrue(entered.wait(2))
        separate.close()
        release.set()
        job.join(3)
        self.assertFalse(job.is_alive())
        self.assertEqual(model.calls, [])
        with self.chat.archive.lock:
            self.assertEqual(self.chat.archive.db.execute('SELECT COUNT(*) FROM private_proactive').fetchone()[0], 0)

    def test_unanswered_proactive_and_three_hour_interval(self):
        row = self.pr.reserve('大肥鱼来啦', self.now)
        self.assertIsNotNone(row)
        self.pr.finish(row, 'confirmed')
        self.assertFalse(self.pr.eligible(self.now + INTERVAL_SECONDS + 1))
        self.add('wxid_friend', '你好呀', self.now + 120)
        self.assertFalse(self.pr.eligible(self.now + INTERVAL_SECONDS - 1))
        self.assertTrue(self.pr.eligible(self.now + INTERVAL_SECONDS + 1))

    def test_interrupted_send_is_not_replayed_and_daily_cap(self):
        row = self.pr.reserve('好呀', self.now)
        self.assertFalse(self.pr.eligible(self.now + INTERVAL_SECONDS + 1))
        self.add('wxid_friend', '你好', self.now + 100)
        self.pr.finish(row, 'unconfirmed')
        self.assertTrue(self.pr.eligible(self.now + INTERVAL_SECONDS + 1))
        for stamp in (self.now - 5 * 3600, self.now - 4 * 3600):
            with self.chat.archive.lock, self.chat.archive.db:
                self.chat.archive.db.execute("INSERT INTO private_proactive(created,last_incoming_id,text,status) VALUES(?,?,?,'confirmed')", (stamp, 0, '测试'))
        self.assertFalse(self.pr.eligible(self.now + INTERVAL_SECONDS + 1))

    def test_pending_file_or_unconfirmed_send_prevents_proactive(self):
        m = self.add('wxid_friend', '处理这份文件', self.now - IDLE_SECONDS - 1)
        with self.chat.archive.lock, self.chat.archive.db:
            self.chat.archive.db.execute("INSERT INTO private_pending VALUES (?,'held')", (m['id'],))
        self.assertFalse(self.pr.eligible(self.now))
        self.chat.ack([m])
        (self.chat.archive.directory / 'file-send-pending').touch()
        self.assertFalse(self.pr.eligible(self.now))

    def test_unread_media_stays_quiet_but_confirmed_file_can_be_context(self):
        m = self.add('wxid_friend', '<msg>private-transport</msg>', self.now - IDLE_SECONDS - 1,
                     kind='文件/链接/卡片')
        self.assertFalse(self.pr.eligible(self.now))
        with self.chat.archive.lock, self.chat.archive.db:
            self.chat.archive.db.execute('CREATE TABLE private_file_tasks(attachment_id INTEGER,status TEXT)')
            self.chat.archive.db.execute("INSERT INTO private_file_tasks VALUES (?,'confirmed')", (m['id'],))
        self.assertTrue(self.pr.eligible(self.now))
        self.assertNotIn('private-transport', str(self.pr.generate('鱼的人格')))
        self.assertNotIn('private-transport', str(self.fast.calls[-1]))

    def test_clear_refusal_persists_until_explicit_resume(self):
        self.add('wxid_friend', '别再给我发消息', self.now - IDLE_SECONDS - 3)
        self.add('wxid_friend', '谢谢', self.now - IDLE_SECONDS - 2)
        self.assertFalse(self.pr.eligible(self.now))
        self.add('wxid_friend', '以后可以主动找我聊天', self.now - IDLE_SECONDS - 1)
        self.assertTrue(self.pr.eligible(self.now))

    def test_draft_and_incoming_must_pass_political_guard(self):
        self.guard.block = True
        self.assertIsNone(self.pr.generate('鱼的人格'))
        self.assertEqual(self.fast.calls, [])
        self.guard.block = False
        self.guard.block_draft = True
        self.assertIsNone(self.pr.generate('鱼的人格'))
        self.assertEqual(len(self.fast.calls), 1)

    def test_do_not_force_message_when_model_declines(self):
        self.fast.result = {'reply': False, 'text': ''}
        self.assertIsNone(self.pr.generate('鱼的人格'))
        with self.chat.archive.lock:
            saved = self.chat.archive.db.execute('SELECT status,text FROM private_proactive').fetchone()
        self.assertEqual((saved['status'], saved['text']), ('cancelled', ''))

    def test_prompt_allows_a_simple_greeting_without_a_complex_topic(self):
        draft = self.pr.generate('鱼的人格')
        self.assertTrue(draft)
        instruction = self.fast.calls[-1]['instruction']
        self.assertIn('没有新鲜话题时简单问候也可以', instruction)
        self.assertIn('对方明显需要安静', instruction)

    def test_proactive_never_claims_to_have_done_web_research(self):
        self.fast.result = {'reply': True, 'text': '我查到了最新的消息。', 'needs_web': True}
        self.assertIsNone(self.pr.generate('鱼的人格'))

    def test_defer_persists_across_restarts_and_does_not_consume_send_quota(self):
        self.pr.defer(self.now)
        self.pr = PrivateProactive(self.chat, fast=self.fast, guard=self.guard)
        self.assertFalse(self.pr.eligible(self.now + 1799))
        self.assertTrue(self.pr.eligible(self.now + 1801))

    def test_cancelled_attempt_has_short_cooldown(self):
        row = self.pr.reserve('好呀', self.now)
        self.pr.finish(row, 'cancelled')
        self.assertFalse(self.pr.eligible(self.now + 10))
        self.assertTrue(self.pr.eligible(self.now + 1801))


if __name__ == '__main__':
    unittest.main()
