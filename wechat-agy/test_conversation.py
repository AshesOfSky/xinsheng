import threading
import queue
import time
import unittest
from types import SimpleNamespace

from conversation import ConversationInbox
from bot import Bot


def message(i, text, at=False):
    return {'id': i, 'member_id': f'm{i}', 'name': f'群友{i}', 'created': time.time(),
            'content': text, 'kind': '文本', 'is_at': at, 'is_self': False}


class ConversationTests(unittest.TestCase):
    def test_burst_keeps_mentions_and_latest_topic(self):
        inbox = ConversationInbox()
        for m in [message(1, '旧话题'), message(2, '@大肥鱼 问题甲', True),
                  message(3, '新的话题'), message(4, '@大肥鱼 问题乙', True)]:
            inbox.put(m)
        batch = inbox.take(threading.Event())
        self.assertEqual([m['id'] for m in batch if m['is_at']], [2, 4])
        self.assertEqual(batch[-1]['id'], 4)
        self.assertEqual(inbox.size(), 0)
        inbox.put(message(5, '又有新进展'))
        inbox.restore_required(batch)
        self.assertEqual([m['id'] for m in inbox.take(threading.Event())], [2, 4, 5])

    def test_mention_bypasses_ordinary_cooldown(self):
        inbox = ConversationInbox()
        inbox.put(message(1, '叫你了', True))
        before = time.monotonic()
        self.assertEqual(inbox.take(threading.Event(), before + 60)[0]['id'], 1)
        self.assertLess(time.monotonic() - before, 0.2)

    def test_generation_failure_retries_unanswered_mention(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.favorite_jobs = queue.Queue()
        bot.config = {'ordinary_reply_seconds': 0}
        bot.jobs = ConversationInbox()
        original = message(1, '@大肥鱼 在吗', True)
        bot.jobs.put(original)
        bot.archive = SimpleNamespace(recent=lambda n: [original], profiles=lambda ids: [],
            record_outgoing=lambda *a: 1, outgoing_status=lambda *a: None)
        bot.last_send = 0
        bot.send_lock = threading.Lock()
        bot.pending_lock = threading.RLock()
        bot.pending_sends = {}
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw: None
        calls, sent = [], []

        def ask(payload, cancel=None):
            if payload['task'] == 'warmup':
                return {'reply': False, 'text': '', 'needs_high': False, 'needs_web': False}
            calls.append(payload)
            if len(calls) == 1:
                raise RuntimeError('temporary network error')
            return {'reply': True, 'text': '大肥鱼在呢', 'needs_high': False, 'needs_web': False}

        def send(text):
            sent.append(text)
            bot.stop.set()
            return True, ''

        bot.reply_model = SimpleNamespace(ask=ask, model='low', last_result={'seconds': 0.1})
        bot.deep_model = None
        bot.bridge = SimpleNamespace(prepare_sender=lambda: None, send=send, last_send_timing={'ui_seconds': 0.1})
        timer = threading.Timer(8, bot.stop.set)
        timer.start()
        try:
            bot.reply_loop()
        finally:
            bot.stop.set()
            timer.cancel()
        self.assertEqual(sent, ['大肥鱼在呢'])
        self.assertEqual([m['id'] for m in calls[1]['unanswered_mentions']], [1])

    def test_topic_change_during_generation_never_sends_old_draft(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.favorite_jobs = queue.Queue()
        bot.config = {'ordinary_reply_seconds': 0}
        bot.jobs = ConversationInbox()
        old = message(1, '聊聊股票', True)
        new = message(2, '换个话题，晚饭吃什么')
        rows, sent, calls = [old], [], []
        bot.archive = SimpleNamespace(recent=lambda n: rows[-n:], profiles=lambda ids: [],
            record_outgoing=lambda *a: 1, outgoing_status=lambda *a: None)
        bot.jobs.put(old)
        bot.last_send = 0
        bot.send_lock = threading.Lock()
        bot.pending_lock = threading.RLock()
        bot.pending_sends = {}
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw: None

        def ask(payload, cancel=None):
            if payload['task'] == 'warmup':
                return {'reply': False, 'text': '', 'needs_high': False, 'needs_web': False}
            calls.append(payload)
            if len(calls) == 1:
                rows.append(new)
                bot.jobs.put(new)
                return {'reply': True, 'text': '过时的股票回答', 'needs_high': False, 'needs_web': False}
            return {'reply': True, 'text': '股票先放一边，晚饭吃面怎么样？', 'needs_high': False, 'needs_web': False}

        bot.reply_model = SimpleNamespace(ask=ask, model='low', last_result={'seconds': 0.1})
        bot.deep_model = None

        def send(text):
            sent.append(text)
            bot.stop.set()
            return True, ''

        bot.bridge = SimpleNamespace(prepare_sender=lambda: None, send=send, last_send_timing={'ui_seconds': 0.1})
        bot.reply_loop()
        self.assertEqual(sent, ['股票先放一边，晚饭吃面怎么样？'])
        self.assertTrue(calls[1]['must_reply'])
        self.assertEqual(calls[1]['unanswered_mentions'][0]['id'], 1)
        self.assertEqual(calls[1]['recent_messages'][-1]['id'], 2)

    def test_low_keeps_chatting_while_high_later_mentions_original_questioner(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.favorite_jobs = queue.Queue()
        bot.config = {'ordinary_reply_seconds': 0}
        bot.jobs = ConversationInbox()
        bot.research_jobs = queue.Queue()
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.last_send = 0
        bot.send_lock = threading.Lock()
        bot.pending_lock = threading.RLock()
        bot.pending_sends = {}
        bot.update_status = lambda **kw: None
        question, chatter = message(1, '请认真计算', True), message(2, '今晚吃火锅')
        rows, sent = [question], []
        high_started, release_high = threading.Event(), threading.Event()
        bot.archive = SimpleNamespace(recent=lambda n: rows[-n:], profiles=lambda ids: [],
            record_outgoing=lambda *a: len(sent)+1, outgoing_status=lambda *a: None)

        def fast(payload, cancel=None):
            if payload['task'] == 'warmup':
                return {'reply':False, 'text':'', 'needs_high':False, 'needs_web':False}
            if payload['new_messages'][-1]['id'] == 1:
                return {'reply':True, 'text':'', 'needs_high':True, 'needs_web':False, 'research_message_ids':[1]}
            self.assertTrue(high_started.is_set())
            self.assertEqual(payload['background_questions'][0]['id'], 1)
            return {'reply':True, 'text':'大肥鱼申请一份虾滑', 'needs_high':False, 'needs_web':False}

        def high(payload, cancel=None):
            high_started.set()
            rows.append(chatter)
            bot.jobs.put(chatter)
            self.assertTrue(release_high.wait(3), 'Low 被 High 阻塞，没能先发送闲聊')
            return {'reply':True, 'text':'算好了，1210', 'sources':[]}

        def send(text, mention_ids=()):
            sent.append((text, list(mention_ids)))
            if len(sent) == 1:
                release_high.set()
            else:
                bot.stop.set()
            return True, ''

        bot.reply_model = SimpleNamespace(ask=fast, model='low', last_result={'seconds':0.1})
        bot.deep_model = SimpleNamespace(ask=high, model='high', last_result={'seconds':1})
        bot.bridge = SimpleNamespace(prepare_sender=lambda:None, send=send, last_send_timing={'ui_seconds':0.1})
        bot.jobs.put(question)
        worker = threading.Thread(target=bot.research_loop, daemon=True)
        worker.start()
        timer = threading.Timer(6, bot.stop.set)
        timer.start()
        try:
            bot.reply_loop()
        finally:
            bot.stop.set()
            release_high.set()
            timer.cancel()
            worker.join(1)
        self.assertEqual(sent, [('大肥鱼申请一份虾滑', []), ('回你刚才的问题：算好了，1210', ['m1'])])


if __name__ == '__main__':
    unittest.main()
