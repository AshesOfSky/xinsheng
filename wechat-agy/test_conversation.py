import threading
import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from conversation import ConversationInbox
from bot import Bot


def message(i, text, at=False):
    return {'id': i, 'member_id': f'm{i}', 'name': f'群友{i}', 'created': time.time(),
            'content': text, 'kind': '文本', 'is_at': at, 'is_self': False}


class AllowGuard:
    def filter(self, batch, context, draft=''):
        return set(), False

    def close(self):
        pass


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
        bot.political_guard = AllowGuard()
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
        bot.political_guard = AllowGuard()
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
        bot.political_guard = AllowGuard()
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

    def test_political_mention_is_silent_in_mixed_batch(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.favorite_jobs = queue.Queue()
        bot.config = {'ordinary_reply_seconds': 0}
        bot.jobs = ConversationInbox()
        political, ordinary = message(1, '@大肥鱼 讨论政治人物', True), message(2, '晚饭吃什么')
        rows, sent, calls = [political, ordinary], [], []
        bot.archive = SimpleNamespace(recent=lambda n: rows[-n:], profiles=lambda ids: [],
            record_outgoing=lambda *a: 1, outgoing_status=lambda *a: None)
        bot.last_send = 0
        bot.send_lock = threading.Lock()
        bot.pending_lock = threading.RLock()
        bot.pending_sends = {}
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw: None
        bot.political_guard = SimpleNamespace(filter=lambda batch, context, draft='':
            ({m['id'] for m in batch if m['id'] == 1}, False))

        def ask(payload, cancel=None):
            if payload['task'] == 'warmup':
                return {'reply':False, 'text':'', 'needs_high':False, 'needs_web':False}
            calls.append(payload)
            return {'reply':True, 'text':'晚饭吃面怎么样？', 'needs_high':False, 'needs_web':False}

        def send(text):
            sent.append(text)
            bot.stop.set()
            return True, ''

        bot.reply_model = SimpleNamespace(ask=ask, model='low', last_result={'seconds':0.1})
        bot.deep_model = None
        bot.bridge = SimpleNamespace(prepare_sender=lambda:None, send=send, last_send_timing={'ui_seconds':0.1})
        bot.jobs.put(political)
        bot.jobs.put(ordinary)
        timer = threading.Timer(6, bot.stop.set)
        timer.start()
        try:
            bot.reply_loop()
        finally:
            bot.stop.set()
            timer.cancel()
        self.assertEqual(sent, ['晚饭吃面怎么样？'])
        self.assertEqual([m['id'] for m in calls[0]['new_messages']], [2])
        self.assertNotIn(1, [m['id'] for m in calls[0]['recent_messages']])
        self.assertFalse(calls[0]['must_reply'])

    def test_political_sticker_draft_is_not_sent_even_for_mention(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.favorite_jobs = queue.Queue()
        bot.config = {'ordinary_reply_seconds': 0}
        bot.jobs = ConversationInbox()
        trigger = message(1, '@大肥鱼 看看这个', True)
        bot.jobs.put(trigger)
        bot.archive = SimpleNamespace(recent=lambda n:[trigger], profiles=lambda ids:[],
            record_outgoing=lambda *a:1, outgoing_status=lambda *a:None)
        bot.last_send = 0
        bot.send_lock = threading.Lock()
        bot.pending_lock = threading.RLock()
        bot.pending_sends = {}
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw:None
        reviewed, sent = [], []
        def guard(batch, context, draft=''):
            if draft:
                reviewed.append(draft)
                bot.stop.set()
                return set(), True
            return set(), False
        bot.political_guard = SimpleNamespace(filter=guard)
        bot.stickers = SimpleNamespace(choices=lambda:[{'code':'S001'}],
            select=lambda code, offered:{'description':'政治人物表情', 'meaning':'相关政治玩笑'})
        def ask(payload, cancel=None):
            if payload['task'] == 'warmup':
                return {'reply':False, 'text':'', 'needs_high':False, 'needs_web':False}
            return {'reply':True, 'text':'', 'sticker_code':'S001', 'needs_high':False, 'needs_web':False}
        bot.reply_model = SimpleNamespace(ask=ask, model='low', last_result={'seconds':0.1})
        bot.deep_model = None
        bot.bridge = SimpleNamespace(prepare_sender=lambda:None, send=lambda text:sent.append(text))
        bot.send_sticker = lambda *args:sent.append('sticker')
        bot.reply_loop()
        self.assertIn('政治人物表情', reviewed[0])
        self.assertEqual(sent, [])

    def test_guard_failure_retains_only_required_message(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.jobs = ConversationInbox()
        bot.update_status = lambda **kw: None
        mention, ordinary = message(1, '@大肥鱼 帮我看看', True), message(2, '闲聊')
        with patch.object(bot.stop, 'wait', return_value=False):
            bot.guard_failure([mention, ordinary], RuntimeError('unavailable'))
        self.assertEqual([m['id'] for m in bot.jobs.take(threading.Event())], [1])

    def test_research_answer_with_political_content_or_source_stays_silent(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.jobs = ConversationInbox()
        bot.research_jobs = queue.Queue()
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw: None
        question = message(1, '@大肥鱼 某体育新闻是怎么回事', True)
        checked = []
        def guard(batch, context, draft=''):
            if draft:
                checked.append(draft)
                bot.stop.set()
                return set(), True
            return set(), False
        bot.political_guard = SimpleNamespace(filter=guard)
        bot.deep_model = SimpleNamespace(
            ask=lambda payload, cancel=None: {'reply':True, 'text':'结果包含政治内容',
                'sources':[{'title':'相关政治报道', 'url':'https://example.org/story'}]},
            last_result={'seconds':0.1, 'steps':[{'tool':'search_web', 'tool_error':None}]})
        compact = {'id':1, 'name':'群友1', 'text':question['content'], 'at_bot':True, 'bot':False}
        bot.research_jobs.put({'request':{'recent_messages':[compact], 'research_questions':[compact]},
                               'targets':[question], 'web':True, 'elapsed':0.1,
                               'generation_started':time.monotonic()})
        bot.research_loop()
        self.assertTrue(checked)
        self.assertIn('相关政治报道', checked[0])
        self.assertEqual(bot.jobs.size(), 0)

    def test_failed_research_result_check_retries_required_without_repeat_search(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.jobs = ConversationInbox()
        bot.research_jobs = queue.Queue()
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw:None
        question = message(1, '@大肥鱼 某体育问题', True)
        calls, generated, delivered = [], [], []
        def guard(batch, context, draft=''):
            calls.append(draft)
            if draft and sum(bool(value) for value in calls) == 1:
                raise RuntimeError('temporary classifier outage')
            return set(), False
        bot.political_guard = SimpleNamespace(filter=guard)
        def research(payload, cancel=None):
            generated.append(1)
            return {'reply':True, 'text':'体育回答',
                    'sources':[{'title':'赛事记录', 'url':'https://example.org/match'}]}
        bot.deep_model = SimpleNamespace(ask=research,
            last_result={'seconds':0.1, 'steps':[{'tool':'search_web', 'tool_error':None}]})
        def put(result):
            delivered.append(result)
            bot.stop.set()
        bot.jobs.put = put
        compact = {'id':1, 'name':'群友1', 'text':question['content'], 'at_bot':True, 'bot':False}
        bot.research_jobs.put({'request':{'recent_messages':[compact], 'research_questions':[compact]},
                               'targets':[question], 'web':True, 'elapsed':0.1,
                               'generation_started':time.monotonic()})
        with patch.object(bot.stop, 'wait', return_value=False):
            bot.research_loop()
        self.assertEqual(generated, [1])
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]['_completed_research']['text'].splitlines()[0], '回你刚才的问题：体育回答')

    def test_mixed_research_result_requeues_only_nonpolitical_question(self):
        bot = Bot.__new__(Bot)
        bot.stop = threading.Event()
        bot.stickers = None
        bot.jobs = ConversationInbox()
        bot.research_jobs = queue.Queue()
        bot.research_pending = {}
        bot.research_lock = threading.Lock()
        bot.update_status = lambda **kw:None
        political = message(1, '@大肥鱼 某事件是怎么回事', True)
        ordinary = message(2, '@大肥鱼 比赛什么时候开始', True)
        delivered = []
        def guard(batch, context, draft=''):
            return ({1} if draft else set()), False
        bot.political_guard = SimpleNamespace(filter=guard)
        bot.deep_model = SimpleNamespace(
            ask=lambda payload, cancel=None:{'reply':True, 'text':'混合回答', 'sources':[]},
            last_result={'seconds':0.1, 'steps':[]})
        def put(result):
            delivered.append(result)
            bot.stop.set()
        bot.jobs.put = put
        compacts = [{'id':m['id'], 'name':m['name'], 'text':m['content'], 'at_bot':True, 'bot':False}
                    for m in (political, ordinary)]
        bot.research_jobs.put({'request':{'recent_messages':compacts, 'research_questions':compacts},
                               'targets':[political, ordinary], 'web':False, 'elapsed':0.1,
                               'generation_started':time.monotonic()})
        bot.research_loop()
        self.assertEqual([m['id'] for m in delivered], [2])
        self.assertNotIn('_completed_research', delivered[0])

    def test_neutral_required_research_failure_or_missing_sources_stays_silent_and_retries(self):
        for failure in ('exception', 'no_sources'):
            with self.subTest(failure=failure):
                bot = Bot.__new__(Bot)
                bot.stop = threading.Event()
                bot.stickers = None
                bot.jobs = ConversationInbox()
                bot.research_jobs = queue.Queue()
                bot.research_lock = threading.Lock()
                question = message(1, '@大肥鱼 某体育新闻是怎么回事', True)
                bot.research_pending = {question['id']:question}
                bot.political_guard = AllowGuard()
                bot.update_status = lambda **kw:None
                def ask(payload, cancel=None):
                    if failure == 'exception':
                        raise RuntimeError('temporary research failure')
                    return {'reply':True, 'text':'体育新闻', 'sources':[]}
                bot.deep_model = SimpleNamespace(ask=ask,
                    last_result={'seconds':0.1, 'steps':[{'tool':'search_web', 'tool_error':None}]})
                compact = {'id':1, 'name':'群友1', 'text':question['content'], 'at_bot':True, 'bot':False}
                job = {'request':{'recent_messages':[compact], 'research_questions':[compact]},
                       'targets':[question], 'web':True, 'elapsed':0.1,
                       'generation_started':time.monotonic()}
                bot.research_jobs.put(job)
                retries = []
                def retry(item):
                    retries.append(item)
                    bot.stop.set()
                bot.research_jobs.put = retry
                with patch.object(bot.stop, 'wait', return_value=False):
                    bot.research_loop()
                self.assertEqual(bot.jobs.size(), 0)
                self.assertEqual(retries, [job])
                self.assertIn(question['id'], bot.research_pending)
                self.assertNotIn('_ready_result', job)


if __name__ == '__main__':
    unittest.main()
